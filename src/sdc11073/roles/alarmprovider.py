import time
import traceback
from threading import Thread, Event
from .. import namespaces
from ..pmtypes import  AlertActivation, AlertConditionKind, AlertSignalPresence, SystemSignalActivation
from ..pmtypes import  AlertSignalManifestation
from . import providerbase


class GenericAlarmProvider(providerbase.ProviderRole):
    WORKERTHREAD_INTERVAL = 1.0 # seconds
    self_check_safety_margin = 1.0  # how many seconds before SelfCheckInterval elapses a new self check is performed.

    def __init__(self, log_prefix):
        super().__init__(log_prefix)

        self._lastSetAlertSignalState = {} # a lookup by alert signal handle , value = time of last call

        self._stopWorker = Event()
        self._workerThread = None

    def initOperations(self, mdib):
        super().initOperations(mdib)
        self._setAlertSystemStatesInitialValues()
        self._setAlertStatesInitialValues()
        self._workerThread = Thread(target=self._workerThreadLoop)
        self._workerThread.daemon = True
        self._workerThread.start()

    def makeMissingOperations(self):
        return []

    def stop(self):
        self._stopWorker.set()
        self._workerThread.join()

    def makeOperationInstance(self, operationDescriptorContainer):
        operationTargetHandle = operationDescriptorContainer.OperationTarget
        operationTargetDescr = self._mdib.descriptions.handle.getOne(operationTargetHandle) # descriptor container
        if operationDescriptorContainer.NODETYPE == namespaces.domTag('SetValueOperationDescriptor'):
            pass
        elif operationDescriptorContainer.NODETYPE == namespaces.domTag('ActivateOperationDescriptor'):
            pass
        elif operationDescriptorContainer.NODETYPE == namespaces.domTag('SetAlertStateOperationDescriptor'):
            if operationTargetDescr.NODETYPE == namespaces.domTag('AlertSignalDescriptor') and operationTargetDescr.SignalDelegationSupported:
                modifiable_data = [d.text for d in operationDescriptorContainer.ModifiableData]
                if 'Presence' in modifiable_data \
                        and 'ActivationState' in modifiable_data \
                        and 'ActualSignalGenerationDelay' in modifiable_data:
                    # ToDo:  check for appropriate code
                    operation = self._mkOperationFromOperationDescriptor(
                        operationDescriptorContainer,
                        currentArgumentHandler=self._delegate_alert_signal,
                        timeoutHandler=self._end_delegate_alert_signal)

                    self._logger.info(f'GenericAlarmProvider: added handler "self._delegate_alert_signal" '
                                      f'for {operationDescriptorContainer} target= {operationTargetDescr} ')
                    return operation

        return None  # None == no handler for this operation instantiated

    def _setAlertSystemStatesInitialValues(self):
        alertSystemStates = self._mdib.states.NODETYPE.get(namespaces.domTag('AlertSystemState'), [])
        for alertSystemState in alertSystemStates:
            if hasattr(alertSystemState, 'SystemSignalActivation'): # attribute not exists in Draft6
                alertSystemState.SystemSignalActivation.append(SystemSignalActivation(manifestation=AlertSignalManifestation.AUD,
                                                                                              state=AlertActivation.ON))

    def _setAlertStatesInitialValues(self):
        """
        - set all AlertConditions ActivationState to 'On','
        - set all local alert Signals ActivationState to 'On'
        - set all remote alert Signals ActivationState to 'Off' (must be explicitly enabled by delegating device)"""
        alert_conditions = self._mdib.states.NODETYPE.get(namespaces.domTag('AlertConditionState'),[]) \
                         + self._mdib.states.NODETYPE.get(namespaces.domTag('LimitAlertConditionState'),[])
        for alert_condition in alert_conditions:
            alert_condition.ActivationState = AlertActivation.ON
        for alert_signal_state in self._mdib.states.NODETYPE.get(namespaces.domTag('AlertSignalState'),[]):
            alert_signal_descr = self._mdib.descriptions.handle.getOne(alert_signal_state.descriptorHandle)
            if alert_signal_descr.SignalDelegationSupported:
                alert_signal_state.Location = 'Rem'
                alert_signal_state.ActivationState = AlertActivation.OFF
                alert_signal_state.Presence = AlertSignalPresence.OFF
            else:
                alert_signal_state.ActivationState = AlertActivation.ON
                alert_signal_state.Presence = AlertSignalPresence.OFF

    def _getDescriptor(self, handle, mdib, transaction):
        """Helper that looks for descriptor first in current transaction, then in mdib.

        Returns first found one or raises KeyError"""
        descriptor = None
        tr_item = transaction.descriptorUpdates.get(handle)
        if tr_item is not None:
            descriptor = tr_item.new
        if descriptor is None:
            # it is not part of this transaction
            descriptor = mdib.descriptions.handle.getOne(handle, allowNone=True)
        if descriptor is None:
            raise KeyError('there is no descriptor for {}'.format(handle))
        return descriptor

    def _getChangedAlertConditionStates(self, transaction):
        result = []
        for item in list(transaction.alertStateUpdates.values()):
            tmp = item.old if item.new is None else item.new
            if tmp.NODETYPE in(namespaces.domTag('AlertConditionState'),
                               namespaces.domTag('LimitAlertConditionState')):
                result.append(tmp)
        return result

    def _findAlertSystemsWithModifications(self, mdib, transaction):
        # find all alert systems with changed states
        alertSystemStates = set()
        changedAlertConditions = self._getChangedAlertConditionStates(transaction)
        for tmp in changedAlertConditions:
            alertDescriptor = self._getDescriptor(tmp.descriptorHandle, mdib, transaction)
            alertSystemDescriptor = self._getDescriptor(alertDescriptor.parentHandle, mdib, transaction)
            if alertSystemDescriptor.handle in transaction.alertStateUpdates:
                tmp_st = transaction.alertStateUpdates[alertSystemDescriptor.handle]
                if tmp_st.new is not None:
                    alertSystemStateContainer = tmp_st.new
                    alertSystemStates.add(alertSystemStateContainer)
            else:
                alertSystemStateContainer = transaction.getAlertState(alertSystemDescriptor.handle)
                alertSystemStates.add(alertSystemStateContainer)
        return alertSystemStates

    def onPreCommit(self, mdib, transaction):
        if not transaction.alertStateUpdates:
            return
        # find all alert systems with changed states
        alertSystemStates = self._findAlertSystemsWithModifications( mdib, transaction)
        if alertSystemStates:
            # add found alert system states to transaction. This is not part of cyclich self check
            self._updateAlertSystemStates(mdib, transaction, alertSystemStates, is_self_check=False)

        # change AlertSignal Settings in order to be compliant with changed Alert Conditions
        changedAlertConditions = self._getChangedAlertConditionStates(transaction)
        for changedAlertCondition in changedAlertConditions:
            self._updateAlertSignals(changedAlertCondition, mdib, transaction)

    def _updateAlertSystemStates(self, mdib, transaction, alertSystemStates, is_self_check=True):
        """
        update alert system states
        :param mdib:
        :param transaction:
        :param alertSystemStates: list of AlertSystemStateContainer instances
        :param is_self_check: if True, LastSelfCheck and SelfCheckCount are set
        :return:
        """
        def _getAlertState(descriptorHandle):
            alertState = None
            tr_item = transaction.getStateTransactionItem(descriptorHandle)
            if tr_item is not None:
                alertState = tr_item.new
            if alertState is None:
                # it is not part of this transaction
                alertState = mdib.states.descriptorHandle.getOne(descriptorHandle, allowNone=True)
            if alertState is None:
                raise RuntimeError('there is no alert state for {}'.format(descriptorHandle))
            return alertState

        for st in alertSystemStates:
            # find all alert condition descriptors of alert system
            all_child_descriptors = mdib.descriptions.parentHandle.get(st.descriptorHandle, list())
            all_child_descriptors.extend([i.new for i in transaction.descriptorUpdates.values() if i.new.parentHandle == st.descriptorHandle])
            all_alert_condition_descr = [d for d in all_child_descriptors if hasattr(d, 'Kind')]
            # select all state containers with technical alarms present
            all_tech_descr = [ d for d in all_alert_condition_descr if d.Kind == AlertConditionKind.TECHNICAL]
            all_tech_states = [_getAlertState(d.handle) for d in all_tech_descr]
            all_tech_states = [s for s in all_tech_states if s is not None]
            all_present_tech_states = [s for s in all_tech_states if s.Presence]
            # select all state containers with physiological alarms present
            all_phys_descr = [ d for d in all_alert_condition_descr if d.Kind == AlertConditionKind.PHYSIOLOGICAL]
            all_phys_states = [_getAlertState(d.handle) for d in all_phys_descr]
            all_phys_states = [ s for s in all_phys_states if s is not None]
            all_present_phys_states = [s for s in all_phys_states if s.Presence]

            st.PresentTechnicalAlarmConditions = [s.descriptorHandle for s in all_present_tech_states]
            st.PresentPhysiologicalAlarmConditions = [s.descriptorHandle for s in all_present_phys_states]
            if is_self_check:
                st.LastSelfCheck = time.time()
                st.SelfCheckCount = 1 if st.SelfCheckCount is None else st.SelfCheckCount + 1

    def _updateAlertSignals(self, changedAlertCondition, mdib, transaction):
        """ Handle alert signals for a changed alert condition.
        This method only changes states of local signals.
        Handling of delegated signals is in the responsibility of the delegated device!"""
        alertSignalDescriptors = mdib.descriptions.ConditionSignaled.get(changedAlertCondition.descriptorHandle, [])
        # separate remote from local
        remoteAlertSignalDescriptors = [a for a in alertSignalDescriptors if a.SignalDelegationSupported]
        localAlertSignalDescriptors = [a for a in alertSignalDescriptors if not a.SignalDelegationSupported]

        # look for active delegations (we only need the Manifestation value here)
        active_delegate_manifestations = []
        for a in remoteAlertSignalDescriptors:
            alertSignalState = mdib.states.descriptorHandle.getOne(a.handle)
            if alertSignalState.Presence != AlertSignalPresence.OFF and alertSignalState.Location == 'Rem':
                active_delegate_manifestations.append(a.Manifestation)

        # this lookup gives the values that a local signal shall have:
        # key = (Cond.Presence, isDelegated): value = (SignalState.ActivationState, SignalState.Presence)
        # see BICEPS standard table 9: valid combinations of alert activation states, alert condition presence, ...
        # this is the relevant subset for our case
        lookup = {(True, True): (AlertActivation.PAUSED, AlertSignalPresence.OFF),
                  (True, False): (AlertActivation.ON, AlertSignalPresence.ON),
                  (False, True): (AlertActivation.PAUSED, AlertSignalPresence.OFF),
                  (False, False): (AlertActivation.ON, AlertSignalPresence.OFF)
                  }
        for a in localAlertSignalDescriptors:
            tr_item = transaction.getStateTransactionItem(a.handle)
            if tr_item is None:
                isDelegated = a.Manifestation in active_delegate_manifestations  # is this local signal delegated?
                activation, presence = lookup[(changedAlertCondition.Presence, isDelegated)]
                alertSignalState = transaction.getAlertState(a.handle)

                if alertSignalState.ActivationState != activation or alertSignalState.Presence != presence:
                    alertSignalState.ActivationState = activation
                    alertSignalState.Presence = presence
                else:
                    # don't change
                    transaction.ungetState(alertSignalState)

    def _pauseFallbackAlertSignals(self, delegableSignalDescriptor, allSignalDescriptors, transaction):
        # look for local fallback signal (same Manifestation), and set it to paused
        for f in self._get_fallback_signals(delegableSignalDescriptor, allSignalDescriptors):
            ss_fallback = transaction.getAlertState(f.handle)
            if ss_fallback.ActivationState != AlertActivation.PAUSED:
                ss_fallback.ActivationState = AlertActivation.PAUSED
            else:
                transaction.ungetState(ss_fallback)

    def _activateFallbackAlertSignals(self, delegableSignalDescriptor, allSignalDescriptors, transaction):
        # look for local fallback signal (same Manifestation), and set it to paused
        for f in self._get_fallback_signals(delegableSignalDescriptor, allSignalDescriptors):
            ss_fallback = transaction.getAlertState(f.handle)
            if ss_fallback.ActivationState == AlertActivation.PAUSED:
                ss_fallback.ActivationState = AlertActivation.ON
            else:
                transaction.ungetState(ss_fallback)

    def _get_fallback_signals(self, delegableSignalDescriptor, allSignalDescriptors):
        if allSignalDescriptors is None:
            allSignalDescriptors = self._mdib.descriptions.ConditionSignaled.get(
                delegableSignalDescriptor.ConditionSignaled, [])
        return [tmp for tmp in allSignalDescriptors if not tmp.SignalDelegationSupported
                and tmp.Manifestation == delegableSignalDescriptor.Manifestation
                and tmp.ConditionSignaled == delegableSignalDescriptor.ConditionSignaled]

    def _delegate_alert_signal(self, operationInstance, value):
        operationTargetHandle = operationInstance.operationTarget
        self._lastSetAlertSignalState[operationTargetHandle] = time.time()
        with self._mdib.mdibUpdateTransaction() as mgr:
            state = mgr.getAlertState(operationTargetHandle)
            self._logger.info('delegate alert signal {} of {} from {} to {}',
                              operationTargetHandle, state, state.ActivationState, value.ActivationState)
            for elem in operationInstance._descriptorContainer.ModifiableData:
                name = elem.text
                tmp = getattr(value, name)
                setattr(state, name, tmp)

            descr = self._mdib.descriptions.handle.getOne(operationTargetHandle)
            if descr.SignalDelegationSupported:
                if value.ActivationState == AlertActivation.ON:
                    self._pauseFallbackAlertSignals(descr, None, mgr)
                else:
                    self._activateFallbackAlertSignals(descr, None, mgr)

    def _end_delegate_alert_signal(self, operationInstance, _):
        operationTargetHandle = operationInstance.operationTarget
        with self._mdib.mdibUpdateTransaction() as mgr:
            state = mgr.getAlertState(operationTargetHandle)
            self._logger.info('timeout alert signal delegate operation={} target={} ',
                              operationInstance.handle, operationTargetHandle)
            state.ActivationState = AlertActivation.OFF
            descr = self._mdib.descriptions.handle.getOne(operationTargetHandle)
            self._activateFallbackAlertSignals(descr, None, mgr)

    def _workerThreadLoop(self):
        # delay start of operation
        time.sleep(5)
        shall_stop = self._stopWorker.wait(timeout=self.WORKERTHREAD_INTERVAL)
        if shall_stop:
            return

        while True:
            shall_stop = self._stopWorker.wait(timeout=self.WORKERTHREAD_INTERVAL)
            if shall_stop:
                return
            self._updateAlertSystemState_CurrentAlerts()

    def _getAlertSystemStates_needingUpdate(self):
        """

        :return: all AlertSystemStateContainers of those last
        """
        alertStatesNeedingUpdate = []
        try:
            all_alert_systems_descr = self._mdib.descriptions.NODETYPE.get(namespaces.domTag('AlertSystemDescriptor'), list())
            for alert_system_descr in all_alert_systems_descr:
                alert_system_state = self._mdib.states.descriptorHandle.getOne(alert_system_descr.handle, allowNone=True)
                self_check_period = alert_system_descr.SelfCheckPeriod
                if self_check_period is not None:
                    last_self_check = alert_system_state.LastSelfCheck or 0.0
                    if time.time() - last_self_check >= self_check_period - self.self_check_safety_margin:
                        alertStatesNeedingUpdate.append(alert_system_state)
        except:
            exc = traceback.format_exc()
            self._logger.error('_getAlertSystemStates_needingUpdate: {}', exc)
        return alertStatesNeedingUpdate

    def _updateAlertSystemState_CurrentAlerts(self):
        """ updates AlertSystemState present alarms list"""
        try:
            with self._mdib.mdibUpdateTransaction() as tr:
                alertStatesNeedingUpdate = self._getAlertSystemStates_needingUpdate()
                if len(alertStatesNeedingUpdate) > 0:
                    tr_states = [tr.getAlertState(s.descriptorHandle) for s in alertStatesNeedingUpdate]
                    self._updateAlertSystemStates(self._mdib, tr, tr_states)
        except:
            exc = traceback.format_exc()
            self._logger.error('_checkAlertStates: {}', exc)
