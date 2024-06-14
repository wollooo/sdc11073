"""
Filename: <provider_GUI.py>
Description: <provides a graphical user interface (GUI) for both an SDC (Service-Oriented Device Connectivity) provider>

Author: <Kevin Wollowski>
Company: <if(is)>
Email: <wollowski@internet-sicherheit.de>
Date: <14.06.2024>
Version: <0.1>

License: <MIT License>
"""


import uuid
import time
import tkinter as tk
from tkinter import ttk, scrolledtext
from sdc11073 import pmtypes
from sdc11073.namespaces import domTag
from sdc11073.sdcdevice import SdcDevice
from sdc11073.mdib import DeviceMdibContainer
from sdc11073.pysoap.soapenvelope import DPWSThisModel, DPWSThisDevice
from sdc11073.location import SdcLocation
from sdc11073.wsdiscovery import WSDiscoverySingleAdapter
from multiprocessing import Process, Pipe
import logging
import queue
import neurokit2 as nk  # Import neurokit2 for generating data

# Define global variables for UUID and logging
baseUUID = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050230}')
my_uuid = uuid.uuid5(baseUUID, "12345")
log_queue = queue.Queue()
process = None  # Variable to hold the process for the SDC device service
parent_conn = None  # To track the parent connection for inter-process communication

class QueueHandler(logging.Handler):
    """ Custom logging handler that sends logs to a queue. """
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        # Emit a logging record to the log queue
        log_entry = self.format(record)
        self.log_queue.put(log_entry)

def update_console():
    """ Update the console output with messages from the log queue. """
    while not log_queue.empty():
        msg = log_queue.get_nowait()
        console_output.configure(state='normal')
        console_output.insert(tk.END, msg + '\n')
        console_output.configure(state='disabled')
        console_output.see(tk.END)
    root.after(100, update_console)

# Initialize logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
log_handler = QueueHandler(log_queue)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_handler.setFormatter(formatter)
logger.addHandler(log_handler)

def simulate_signals(duration_ecg=10, heart_rate=160, duration_rsp=10, respiratory_rate=15, duration_emg=10, burst_number=2, burst_duration=1):
    """ Simulate ECG, respiratory, and EMG signals. """
    ecg_signal = nk.ecg_simulate(duration=duration_ecg, heart_rate=heart_rate, method="simple")
    rsp_signal = nk.rsp_simulate(duration=duration_rsp, respiratory_rate=respiratory_rate, method="breathmetrics")
    emg_signal = nk.emg_simulate(duration=duration_emg, burst_number=burst_number, burst_duration=burst_duration)
    return iter(ecg_signal), iter(rsp_signal), iter(emg_signal)

def sdc_device_service(conn, adapter, manufacturer, model_name, model_version, model_url, firmware_version, friendly_name,
                       initial_metric_value, active_determination_period, validity, activation_state):
    """ 
    SDC device service that simulates medical device behavior and updates metrics. 
    Runs in a separate process.
    """
    import logging
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    child_logger = logging.getLogger()

    try:
        # Generate simulated signals
        ecg_signal_iterator, rsp_signal_iterator, emg_signal_iterator = simulate_signals()

        conn.send("SDC Device service started.")
        child_logger.info("SDC Device service started.")

        # Initialize WS-Discovery and the SDC device
        myDiscovery = WSDiscoverySingleAdapter(adapter)
        myDiscovery.start()
        my_mdib = DeviceMdibContainer.fromMdibFile("mdib.xml")

        dpwsModel = DPWSThisModel(manufacturer=manufacturer,
                                  manufacturerUrl='www.draeger.com',
                                  modelName=model_name,
                                  modelNumber=model_version,
                                  modelUrl=model_url,
                                  presentationUrl=model_url)
        dpwsDevice = DPWSThisDevice(friendlyName=friendly_name,
                                    firmwareVersion=firmware_version,
                                    serialNumber='12345')
        sdcDevice = SdcDevice(ws_discovery=myDiscovery,
                              my_uuid=my_uuid,
                              model=dpwsModel,
                              device=dpwsDevice,
                              deviceMdibContainer=my_mdib)
        sdcDevice.startAll()
        setLocalEnsembleContext(my_mdib, "MyEnsemble")
        sdcDevice.setLocation(SdcLocation(fac='HOSP', poc='CU2', bed='BedSim'))

        numMetrDescr = domTag("NumericMetricDescriptor")
        allMetricDescrs = [c for c in my_mdib.descriptions.objects if c.NODETYPE == numMetrDescr]

        with my_mdib.mdibUpdateTransaction() as mgr:
            # Initialize metrics
            for metricDescr in allMetricDescrs:
                st = mgr.getMetricState(metricDescr.handle)
                st.mkMetricValue()
                st.metricValue.Value = initial_metric_value
                st.metricValue.ActiveDeterminationPeriod = active_determination_period
                st.metricValue.Validity = validity
                st.ActivationState = activation_state

        while not conn.poll():
            # Continuously update metrics with simulated data
            try:
                ecg_value = next(ecg_signal_iterator)
            except StopIteration:
                ecg_signal_iterator = iter(simulate_signals()[0])
                ecg_value = next(ecg_signal_iterator)

            try:
                rsp_value = next(rsp_signal_iterator)
            except StopIteration:
                rsp_signal_iterator = iter(simulate_signals()[1])
                rsp_value = next(rsp_signal_iterator)

            try:
                emg_value = next(emg_signal_iterator)
            except StopIteration:
                emg_signal_iterator = iter(simulate_signals()[2])
                emg_value = next(emg_signal_iterator)

            with my_mdib.mdibUpdateTransaction() as mgr:
                for metricDescr in allMetricDescrs:
                    if metricDescr.handle == "numeric.ch0.vmd0":
                        st = mgr.getMetricState(metricDescr.handle)
                        st.metricValue.Value = ecg_value
                        conn.send(f"Updated {metricDescr.handle} metric value to {ecg_value:.2f}")
                        child_logger.info(f"Updated {metricDescr.handle} metric value to {ecg_value:.2f}")
                    elif metricDescr.handle == "numeric.ch0.vmd1":
                        st = mgr.getMetricState(metricDescr.handle)
                        st.metricValue.Value = rsp_value
                        conn.send(f"Updated {metricDescr.handle} metric value to {rsp_value:.2f}")
                        child_logger.info(f"Updated {metricDescr.handle} metric value to {rsp_value:.2f}")
                    elif metricDescr.handle == "numeric.ch1.vmd0":
                        st = mgr.getMetricState(metricDescr.handle)
                        st.metricValue.Value = emg_value
                        conn.send(f"Updated {metricDescr.handle} metric value to {emg_value:.2f}")
                        child_logger.info(f"Updated {metricDescr.handle} metric value to {emg_value:.2f}")

            # Reduce the sleep time to send updates more frequently
            time.sleep(0.01)

        sdcDevice.stopAll()
        conn.send("SDC Device service stopped.")
        child_logger.info("SDC Device service stopped.")
        conn.close()
    except Exception as e:
        conn.send(f"Error: {str(e)}")
        child_logger.error(f"Error: {str(e)}")
        conn.close()

def setLocalEnsembleContext(mdib, ensemble):
    """ Set the local ensemble context in the MDIB. """
    descriptorContainer = mdib.descriptions.NODETYPE.getOne(domTag('EnsembleContextDescriptor'))
    if not descriptorContainer:
        logger.error("No ensemble contexts in mdib")
        return
    allEnsembleContexts = mdib.contextStates.descriptorHandle.get(descriptorContainer.handle, [])
    with mdib.mdibUpdateTransaction() as mgr:
        # Disassociate all existing ensemble contexts
        associatedEnsembles = [l for l in allEnsembleContexts if l.ContextAssociation == pmtypes.ContextAssociation.ASSOCIATED]
        for l in associatedEnsembles:
            ensembleContext = mgr.getContextState(l.descriptorHandle, l.Handle)
            ensembleContext.ContextAssociation = pmtypes.ContextAssociation.DISASSOCIATED
            ensembleContext.UnbindingMdibVersion = mdib.mdibVersion + 1
            ensembleContext.BindingEndTime = time.time()

        # Associate the new ensemble context
        newEnsState = mgr.getContextState(descriptorContainer.handle)
        newEnsState.ContextAssociation = 'Assoc'
        newEnsState.Identification = [pmtypes.InstanceIdentifier(root="1.2.3", extensionString=ensemble)]

def on_start_button():
    """ Start the SDC device service when the start button is clicked. """
    global process, parent_conn
    if process is not None and process.is_alive():
        return

    # Retrieve values from the GUI
    adapter = adapter_entry.get()
    manufacturer = manufacturer_entry.get()
    model_name = model_name_entry.get()
    model_version = model_version_entry.get()
    model_url = model_url_entry.get()
    firmware_version = firmware_version_entry.get()
    friendly_name = friendly_name_entry.get()
    metric_value = float(metric_value_entry.get())
    active_determination_period = active_determination_period_entry.get()
    validity = validity_entry.get()
    activation_state = activation_state_entry.get()

    # Create a new process for the SDC device service
    parent_conn, child_conn = Pipe()
    process = Process(target=sdc_device_service, args=(child_conn, adapter, manufacturer, model_name, model_version, model_url, firmware_version, friendly_name, metric_value, active_determination_period, validity, activation_state))
    process.start()
    update_status_label("Started", "green")

    # Clear console
    console_output.configure(state='normal')
    console_output.delete(1.0, tk.END)
    console_output.configure(state='disabled')

    root.after(100, lambda: poll_pipe(parent_conn))

def poll_pipe(conn):
    """ Poll the pipe for messages from the SDC device service. """
    try:
        if conn.poll():
            msg = conn.recv()
            logger.info(msg)
            # Update the metric value in the GUI if received
            if "Updated numeric.ch0.vmd0 metric value to" in msg:
                new_value = float(msg.split()[-1])
                metric_value_entry.delete(0, tk.END)
                metric_value_entry.insert(0, f"{new_value:.2f}")
        if process and process.is_alive():
            root.after(100, lambda: poll_pipe(conn))
        else:
            conn.close()
    except EOFError:
        # Handle case where connection is closed
        logger.info("Connection closed.")
    except Exception as e:
        logger.error(f"Polling error: {e}")

def on_stop_button():
    """ Stop the SDC device service when the stop button is clicked. """
    global process, parent_conn
    if process is not None:
        process.terminate()
        process.join()
        process = None
        update_status_label("Stopped", "red")
        logger.info("SDC Device service terminated.")
        if parent_conn:
            parent_conn.close()
            parent_conn = None

def update_status_label(status, color):
    """ Update the status label in the GUI. """
    status_label.config(text=f"Status: {status}", foreground=color)

# GUI Setup using tkinter
root = tk.Tk()
root.title("SDC Provider GUI")

main_frame = ttk.Frame(root, padding="10 10 10 10")
main_frame.grid(column=0, row=0, sticky=(tk.W, tk.E, tk.N, tk.S))

# UUID
uuid_label = ttk.Label(main_frame, text=f"UUID: {my_uuid}")
uuid_label.grid(column=0, row=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)

# Adapter
adapter_label = ttk.Label(main_frame, text="Adapter")
adapter_label.grid(column=0, row=1, sticky=tk.W, pady=5)
adapter_entry = ttk.Entry(main_frame)
adapter_entry.insert(0, "Ethernet")
adapter_entry.grid(column=1, row=1, sticky=(tk.W, tk.E), pady=5)

# Manufacturer
manufacturer_label = ttk.Label(main_frame, text="Manufacturer")
manufacturer_label.grid(column=0, row=2, sticky=tk.W, pady=5)
manufacturer_entry = ttk.Entry(main_frame)
manufacturer_entry.insert(0, "Draeger")
manufacturer_entry.grid(column=1, row=2, sticky=(tk.W, tk.E), pady=5)

# Model Name
model_name_label = ttk.Label(main_frame, text="Model Name")
model_name_label.grid(column=0, row=3, sticky=tk.W, pady=5)
model_name_entry = ttk.Entry(main_frame)
model_name_entry.insert(0, "TestDevice")
model_name_entry.grid(column=1, row=3, sticky=(tk.W, tk.E), pady=5)

# Model Version
model_version_label = ttk.Label(main_frame, text="Model Version")
model_version_label.grid(column=0, row=4, sticky=tk.W, pady=5)
model_version_entry = ttk.Entry(main_frame)
model_version_entry.insert(0, "1.0")
model_version_entry.grid(column=1, row=4, sticky=(tk.W, tk.E), pady=5)

# Model URL
model_url_label = ttk.Label(main_frame, text="Model URL")
model_url_label.grid(column=0, row=5, sticky=tk.W, pady=5)
model_url_entry = ttk.Entry(main_frame)
model_url_entry.insert(0, "www.draeger.com/model")
model_url_entry.grid(column=1, row=5, sticky=(tk.W, tk.E), pady=5)

# Firmware Version
firmware_version_label = ttk.Label(main_frame, text="Firmware Version")
firmware_version_label.grid(column=0, row=6, sticky=tk.W, pady=5)
firmware_version_entry = ttk.Entry(main_frame)
firmware_version_entry.insert(0, "Version1")
firmware_version_entry.grid(column=1, row=6, sticky=(tk.W, tk.E), pady=5)

# Friendly Name
friendly_name_label = ttk.Label(main_frame, text="Friendly Name")
friendly_name_label.grid(column=0, row=7, sticky=tk.W, pady=5)
friendly_name_entry = ttk.Entry(main_frame)
friendly_name_entry.insert(0, "TestDevice")
friendly_name_entry.grid(column=1, row=7, sticky=(tk.W, tk.E), pady=5)

# Metric Parameters
metric_params_label = ttk.Label(main_frame, text="Metric Parameters")
metric_params_label.grid(column=0, row=8, columnspan=2, sticky=tk.W, pady=10)

# Metric Value
metric_value_label = ttk.Label(main_frame, text="Metric Value")
metric_value_label.grid(column=0, row=9, sticky=tk.W, pady=5)
metric_value_entry = ttk.Entry(main_frame)
metric_value_entry.insert(0, "0.0")
metric_value_entry.grid(column=1, row=9, sticky=(tk.W, tk.E), pady=5)

# Active Determination Period
active_determination_period_label = ttk.Label(main_frame, text="Active Determination Period")
active_determination_period_label.grid(column=0, row=10, sticky=tk.W, pady=5)
active_determination_period_entry = ttk.Entry(main_frame)
active_determination_period_entry.insert(0, "1494554822450")
active_determination_period_entry.grid(column=1, row=10, sticky=(tk.W, tk.E), pady=5)

# Validity
validity_label = ttk.Label(main_frame, text="Validity")
validity_label.grid(column=0, row=11, sticky=tk.W, pady=5)
validity_entry = ttk.Entry(main_frame)
validity_entry.insert(0, "Vld")
validity_entry.grid(column=1, row=11, sticky=(tk.W, tk.E), pady=5)

# Activation State
activation_state_label = ttk.Label(main_frame, text="Activation State")
activation_state_label.grid(column=0, row=12, sticky=tk.W, pady=5)
activation_state_entry = ttk.Entry(main_frame)
activation_state_entry.insert(0, "On")
activation_state_entry.grid(column=1, row=12, sticky=(tk.W, tk.E), pady=5)

# Status Label
status_label = ttk.Label(main_frame, text="Status: Stopped", foreground='red')
status_label.grid(column=0, row=13, columnspan=2, sticky=(tk.W, tk.E), pady=5)

# Start and Stop Buttons
button_frame = ttk.Frame(main_frame)
button_frame.grid(column=0, row=14, columnspan=2, pady=10)

start_button = ttk.Button(button_frame, text="Start", command=on_start_button)
start_button.pack(side=tk.LEFT, padx=5)

stop_button = ttk.Button(button_frame, text="Stop", command=on_stop_button)
stop_button.pack(side=tk.LEFT, padx=5)

# Console Output
console_label = ttk.Label(main_frame, text="Console Log")
console_label.grid(column=0, row=15, columnspan=2, sticky=tk.W, pady=5)

console_output = scrolledtext.ScrolledText(main_frame, state='disabled', width=100, height=10)
console_output.grid(column=0, row=16, columnspan=2, sticky=(tk.W, tk.E), pady=5)

# Configure grid weight for resizing
root.columnconfigure(0, weight=1)
root.rowconfigure(0, weight=1)
main_frame.columnconfigure(1, weight=1)

# Update the console with messages from the log queue
root.after(50, update_console)

root.mainloop()
