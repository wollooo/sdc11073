import uuid
import time
from sdc11073 import pmtypes
from sdc11073.namespaces import domTag
from sdc11073.sdcdevice import SdcDevice
from sdc11073.mdib import DeviceMdibContainer
from sdc11073.pysoap.soapenvelope import DPWSThisModel, DPWSThisDevice
from sdc11073.location import SdcLocation
from sdc11073.wsdiscovery import WSDiscoverySingleAdapter
# example SDC provider (device) that sends out metrics every now and then


# The provider we use, should match the one in consumer example
# The UUID is created from a base
baseUUID = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050230}')
my_uuid = uuid.uuid5(baseUUID, "12345")



# setting the local ensemble context upfront
def setLocalEnsembleContext(mdib, ensemble):
    descriptorContainer = mdib.descriptions.NODETYPE.getOne(domTag('EnsembleContextDescriptor'))
    if not descriptorContainer:
        print("No ensemble contexts in mdib")
        return
    allEnsembleContexts = mdib.contextStates.descriptorHandle.get(descriptorContainer.handle, [])
    with mdib.mdibUpdateTransaction() as mgr:
        # set all to currently associated Locations to Disassociated
        associatedEnsembles = [l for l in allEnsembleContexts if
                               l.ContextAssociation == pmtypes.ContextAssociation.ASSOCIATED]
        for l in associatedEnsembles:
            ensembleContext = mgr.getContextState(l.descriptorHandle, l.Handle)
            ensembleContext.ContextAssociation = pmtypes.ContextAssociation.DISASSOCIATED
            ensembleContext.UnbindingMdibVersion = mdib.mdibVersion + 1
            ensembleContext.BindingEndTime = time.time()

        newEnsState = mgr.getContextState(descriptorContainer.handle)  # this creates a new location state
        newEnsState.ContextAssociation = 'Assoc'
        newEnsState.Identification = [pmtypes.InstanceIdentifier(root="1.2.3", extensionString=ensemble)]


if __name__ == '__main__':
    # start with discovery (MDPWS) that is running on the named adapter "Ethernet" (replace as you need it on your machine, e.g. "enet0" or "Ethernet")
    myDiscovery = WSDiscoverySingleAdapter("Ethernet")
    # start the discovery
    myDiscovery.start()
    # create a local mdib that will be sent out on the network, the mdib is based on a XML file
    my_mdib = DeviceMdibContainer.fromMdibFile("mdib.xml")
    print ("My UUID is {}".format(my_uuid))
    # set a location context to allow easy discovery
    my_location = SdcLocation(fac='HOSP', poc='CU2', bed='BedSim')
    # set model information for discovery
    dpwsModel = DPWSThisModel(manufacturer='Draeger',
                              manufacturerUrl='www.draeger.com',
                              modelName='TestDevice',
                              modelNumber='1.0',
                              modelUrl='www.draeger.com/model',
                              presentationUrl='www.draeger.com/model/presentation')
    dpwsDevice = DPWSThisDevice(friendlyName='TestDevice',
                                firmwareVersion='Version1',
                                serialNumber='12345')
    # create a device (provider) class that will do all the SDC magic
    sdcDevice = SdcDevice(ws_discovery=myDiscovery,
                          my_uuid=my_uuid,
                          model=dpwsModel,
                          device=dpwsDevice,
                          deviceMdibContainer=my_mdib)
    # start the local device and make it discoverable
    sdcDevice.startAll()
    # set the local ensemble context to ease discovery based on ensemble ID
    setLocalEnsembleContext(my_mdib, "MyEnsemble")
    # set the location on our device
    sdcDevice.setLocation(my_location) 
    # create one local numeric metric that will change later on
    numMetrDescr = domTag("NumericMetricDescriptor")
    # get all metrics from the mdib (as described in the file)
    allMetricDescrs = [c for c in my_mdib.descriptions.objects if c.NODETYPE == numMetrDescr]
    # now change all the metrics in one transaction
    with my_mdib.mdibUpdateTransaction() as mgr:
        for metricDescr in allMetricDescrs:
            # get the metric state of this specific metric
            st = mgr.getMetricState(metricDescr.handle)
            # create a value in case it is not there yet
            st.mkMetricValue()
            # set the value and some other fields to a fixed value
            st.metricValue.Value = 1.0
            st.metricValue.ActiveDeterminationPeriod = "1494554822450"
            st.metricValue.Validity = 'Vld'
            st.ActivationState = "On"
    metricValue = 0
    # now iterate forever and change the value every few seconds
    while True:
        metricValue += 1
        with my_mdib.mdibUpdateTransaction() as mgr:
            for metricDescr in allMetricDescrs:
                st = mgr.getMetricState(metricDescr.handle)
                st.metricValue.Value = metricValue
        time.sleep(5)
