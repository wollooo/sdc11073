"""
Filename: <provider_GUI.py>
Description: <provides a graphical user interface (GUI) for both an SDC (Service-Oriented Device Connectivity) provider>

Author: <Kevin Wollowski>
Company: <if(is)>
Email: <wollowski@internet-sicherheit.de>
Date: <25.06.2024>
Version: <0.3>

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
process = None
parent_conn = None
attack_active = False
selected_handle = ""
attack_value = ""

class QueueHandler(logging.Handler):
    """ Custom logging handler that sends logs to a queue. """
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        log_entry = self.format(record)
        self.log_queue.put(log_entry)

def update_console():
    """ Update the console output with messages from the log queue. """
    while not log_queue.empty():
        log_item = log_queue.get_nowait()
        # Determine if log_item is a tuple (msg, color) or just a string message
        if isinstance(log_item, tuple):
            msg, color = log_item
        else:
            msg, color = log_item, 'black'
        
        for console in [console_output, attack_console_output]:
            console.configure(state='normal')
            console.insert(tk.END, msg + '\n', color)
            console.tag_config(color, foreground=color)
            console.configure(state='disabled')
            console.see(tk.END)
    root.after(100, update_console)

# Initialize logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
log_handler = QueueHandler(log_queue)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_handler.setFormatter(formatter)
logger.addHandler(log_handler)

def simulate_signals(duration_ecg=10, heart_rate=120, duration_spo2=10, mean_spo2=96, std_spo2=1, duration_bp=10, mean_systolic=80, std_systolic=10):
    """ Simulate ECG, SpO2, and Blood Pressure signals. """
    ecg_signal = nk.ecg_simulate(duration=duration_ecg, heart_rate=heart_rate, sampling_rate=40, method="simple")
    spo2_signal = nk.signal_simulate(duration=duration_spo2, sampling_rate=40, frequency=0.1, noise=std_spo2) + mean_spo2 - std_spo2 / 2
    bp_systolic_signal = nk.signal_simulate(duration=duration_bp, sampling_rate=40, frequency=0.2, noise=std_systolic) + mean_systolic - std_systolic / 2
    return iter(ecg_signal), iter(spo2_signal), iter(bp_systolic_signal)

def sdc_device_service(conn, adapter, manufacturer, model_name, model_version, model_url, firmware_version, friendly_name,
                       initial_metric_value, active_determination_period, validity, activation_state, runtime_conn):
    """ SDC device service that simulates medical device behavior and updates metrics. """
    
    global selected_handle, attack_value, attack_active  # Ensure global variables are declared

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    child_logger = logging.getLogger()

    try:
        ecg_signal_iterator, spo2_signal_iterator, bp_systolic_signal_iterator = simulate_signals()

        conn.send("SDC Device service started.")
        child_logger.info("SDC Device service started.")

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

        num_metr_descr = domTag("NumericMetricDescriptor")
        all_metric_descrs = [c for c in my_mdib.descriptions.objects if c.NODETYPE == num_metr_descr]

        with my_mdib.mdibUpdateTransaction() as mgr:
            for metric_descr in all_metric_descrs:
                st = mgr.getMetricState(metric_descr.handle)
                st.mkMetricValue()
                st.metricValue.Value = initial_metric_value
                st.metricValue.ActiveDeterminationPeriod = active_determination_period
                st.metricValue.Validity = validity
                st.ActivationState = activation_state

        while not conn.poll():
            if runtime_conn.poll():
                runtime_command, runtime_handle, runtime_value = runtime_conn.recv()
                if runtime_command == "start_attack":
                    attack_active = True
                    selected_handle = runtime_handle
                    attack_value = runtime_value
                elif runtime_command == "stop_attack":
                    attack_active = False

            try:
                ecg_value = next(ecg_signal_iterator)
            except StopIteration:
                ecg_signal_iterator = iter(simulate_signals()[0])
                ecg_value = next(ecg_signal_iterator)

            try:
                spo2_value = next(spo2_signal_iterator)
            except StopIteration:
                spo2_signal_iterator = iter(simulate_signals()[1])
                spo2_value = next(spo2_signal_iterator)

            try:
                bp_systolic_value = next(bp_systolic_signal_iterator)
            except StopIteration:
                bp_systolic_signal_iterator = iter(simulate_signals()[2])
                bp_systolic_value = next(bp_systolic_signal_iterator)

            with my_mdib.mdibUpdateTransaction() as mgr:
                for metric_descr in all_metric_descrs:
                    # Debug print for attack variables
                    # print(f"DEBUG - Inside sdc_device_service: Attack Active: {attack_active}, Selected Handle: {selected_handle}, Attack Value: {attack_value}")
                    # child_logger.debug(f"DEBUG - Inside sdc_device_service: Attack Active: {attack_active}, Selected Handle: {selected_handle}, Attack Value: {attack_value}")
                    
                    if metric_descr.handle == "numeric.ch0.vmd0":
                        st = mgr.getMetricState(metric_descr.handle)
                        st.metricValue.Value = float(attack_value) if (attack_active and selected_handle == "numeric.ch0.vmd0") else ecg_value
                        conn.send(f"Updated {metric_descr.handle} metric value to {st.metricValue.Value:.2f}")
                        child_logger.info(f"Updated {metric_descr.handle} metric value to {st.metricValue.Value:.2f}")
                    elif metric_descr.handle == "numeric.ch0.vmd1":
                        st = mgr.getMetricState(metric_descr.handle)
                        st.metricValue.Value = float(attack_value) if (attack_active and selected_handle == "numeric.ch0.vmd1") else spo2_value
                        conn.send(f"Updated {metric_descr.handle} metric value to {st.metricValue.Value:.2f}")
                        child_logger.info(f"Updated {metric_descr.handle} metric value to {st.metricValue.Value:.2f}")
                    elif metric_descr.handle == "numeric.ch1.vmd0":
                        st = mgr.getMetricState(metric_descr.handle)
                        bloodpressure_value = float(attack_value) if (attack_active and selected_handle == "numeric.ch1.vmd0") else bp_systolic_value
                        st.metricValue.Value = bloodpressure_value
                        conn.send(f"Updated {metric_descr.handle} metric value to {st.metricValue.Value:.2f}")
                        child_logger.info(f"Updated {metric_descr.handle} metric value to {st.metricValue.Value:.2f}")

            time.sleep(0.3)  # Adjusted time for more realistic updates

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
        associatedEnsembles = [l for l in allEnsembleContexts if l.ContextAssociation == pmtypes.ContextAssociation.ASSOCIATED]
        for l in associatedEnsembles:
            ensembleContext = mgr.getContextState(l.descriptorHandle, l.Handle)
            ensembleContext.ContextAssociation = pmtypes.ContextAssociation.DISASSOCIATED
            ensembleContext.UnbindingMdibVersion = mdib.mdibVersion + 1
            ensembleContext.BindingEndTime = time.time()

        newEnsState = mgr.getContextState(descriptorContainer.handle)
        newEnsState.ContextAssociation = 'Assoc'
        newEnsState.Identification = [pmtypes.InstanceIdentifier(root="1.2.3", extensionString=ensemble)]

def on_start_button():
    """ Handle start button press to start the SDC device service. """
    global process, parent_conn, runtime_conn
    if process is not None and process.is_alive():
        return

    adapter = adapter_entry.get()
    manufacturer = manufacturer_entry.get()
    model_name = model_name_entry.get()
    model_version = model_version_entry.get()
    model_url = model_url_entry.get()
    firmware_version = firmware_version_entry.get()
    friendly_name = friendly_name_entry.get()
    active_determination_period = active_determination_period_entry.get()
    validity = validity_entry.get()
    activation_state = activation_state_entry.get()

    parent_conn, child_conn = Pipe()
    runtime_conn = Pipe()  # Create a pipe for runtime commands
    process = Process(target=sdc_device_service, args=(child_conn, adapter, manufacturer, model_name, model_version, model_url, firmware_version, friendly_name, 0.0, active_determination_period, validity, activation_state, runtime_conn[1]))
    process.start()
    update_status_label("Started", "green")

    for console in [console_output, attack_console_output]:
        console.configure(state='normal')
        console.delete(1.0, tk.END)
        console.configure(state='disabled')

    root.after(100, lambda: poll_pipe(parent_conn))
    update_attack_buttons_state()

def poll_pipe(conn):
    """ Poll the pipe for messages from the SDC device service. """
    try:
        if conn.poll():
            msg = conn.recv()
            logger.info(msg)
            if "Updated numeric." in msg:
                parts = msg.split()
                handle = parts[1]
                new_value = parts[-1]
                if handle == "numeric.ch0.vmd0":
                    ecg_var.set(f"{new_value}")
                elif handle == "numeric.ch0.vmd1":
                    spo2_var.set(f"{new_value}")
                elif handle == "numeric.ch1.vmd0":
                    bp_var.set(f"{new_value}")
        if process and process.is_alive():
            root.after(100, lambda: poll_pipe(conn))
        else:
            conn.close()
    except EOFError:
        logger.info("Connection closed.")
    except Exception as e:
        logger.error(f"Polling error: {e}")

def on_stop_button():
    """ Handle stop button press to stop the SDC device service. """
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
        update_attack_buttons_state()

def update_status_label(status, color):
    """ Update the status label in the GUI. """
    status_label.config(text=f"Status: {status}", foreground=color)
    update_attack_buttons_state()

def start_attack(handle, value):
    """ Start sending the specified value to the selected handle. """
    global runtime_conn
    runtime_conn[0].send(("start_attack", handle, value))
    print(f"Starting attack on {handle} with value {value}")
    attack_status_label.config(text=f"Attack on {handle}: Active", foreground='green')
    logger.info(f"Starting attack on {handle} with value {value}")
    log_queue.put((f"Starting attack on {handle} with value {value}", 'red'))  # Add color info to log queue

def stop_attack():
    """ Stop the attack. """
    global runtime_conn
    runtime_conn[0].send(("stop_attack", None, None))
    print("Stopping attack")
    attack_status_label.config(text="Attack Status: Inactive", foreground='red')
    logger.info("Stopping attack")
    log_queue.put(("Stopping attack", 'green'))  # Add color info to log queue

def update_attack_buttons_state():
    """ Enable/Disable attack buttons based on main service status. """
    if status_label.cget("text").endswith("Started"):
        start_attack_button.state(["!disabled"])
        stop_attack_button.state(["!disabled"])
    else:
        start_attack_button.state(["disabled"])
        stop_attack_button.state(["disabled"])

# GUI Setup using tkinter
root = tk.Tk()
root.title("SDC Provider GUI")

# Create a notebook (tab control)
notebook = ttk.Notebook(root)
notebook.grid(column=0, row=0, sticky=(tk.W, tk.E, tk.N, tk.S))

main_frame = ttk.Frame(notebook, padding="10 10 10 10")
notebook.add(main_frame, text='Main')
attack_frame = ttk.Frame(notebook, padding="10 10 10 10")
notebook.add(attack_frame, text='Simulate Attack')

# Main tab components
uuid_label = ttk.Label(main_frame, text=f"UUID: {my_uuid}")
uuid_label.grid(column=0, row=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)

adapter_label = ttk.Label(main_frame, text="Adapter")
adapter_label.grid(column=0, row=1, sticky=tk.W, pady=5)
adapter_entry = ttk.Entry(main_frame)
adapter_entry.insert(0, "Ethernet")
adapter_entry.grid(column=1, row=1, sticky=(tk.W, tk.E), pady=5)

manufacturer_label = ttk.Label(main_frame, text="Manufacturer")
manufacturer_label.grid(column=0, row=2, sticky=tk.W, pady=5)
manufacturer_entry = ttk.Entry(main_frame)
manufacturer_entry.insert(0, "Draeger")
manufacturer_entry.grid(column=1, row=2, sticky=(tk.W, tk.E), pady=5)

model_name_label = ttk.Label(main_frame, text="Model Name")
model_name_label.grid(column=0, row=3, sticky=tk.W, pady=5)
model_name_entry = ttk.Entry(main_frame)
model_name_entry.insert(0, "TestDevice")
model_name_entry.grid(column=1, row=3, sticky=(tk.W, tk.E), pady=5)

model_version_label = ttk.Label(main_frame, text="Model Version")
model_version_label.grid(column=0, row=4, sticky=tk.W, pady=5)
model_version_entry = ttk.Entry(main_frame)
model_version_entry.insert(0, "1.0")
model_version_entry.grid(column=1, row=4, sticky=(tk.W, tk.E), pady=5)

model_url_label = ttk.Label(main_frame, text="Model URL")
model_url_label.grid(column=0, row=5, sticky=tk.W, pady=5)
model_url_entry = ttk.Entry(main_frame)
model_url_entry.insert(0, "www.draeger.com/model")
model_url_entry.grid(column=1, row=5, sticky=(tk.W, tk.E), pady=5)

firmware_version_label = ttk.Label(main_frame, text="Firmware Version")
firmware_version_label.grid(column=0, row=6, sticky=tk.W, pady=5)
firmware_version_entry = ttk.Entry(main_frame)
firmware_version_entry.insert(0, "Version1")
firmware_version_entry.grid(column=1, row=6, sticky=(tk.W, tk.E), pady=5)

friendly_name_label = ttk.Label(main_frame, text="Friendly Name")
friendly_name_label.grid(column=0, row=7, sticky=tk.W, pady=5)
friendly_name_entry = ttk.Entry(main_frame)
friendly_name_entry.insert(0, "TestDevice")
friendly_name_entry.grid(column=1, row=7, sticky=(tk.W, tk.E), pady=5)

metric_params_label = ttk.Label(main_frame, text="Metric Parameters")
metric_params_label.grid(column=0, row=8, columnspan=2, sticky=tk.W, pady=10)

active_determination_period_label = ttk.Label(main_frame, text="Active Determination Period")
active_determination_period_label.grid(column=0, row=9, sticky=tk.W, pady=5)
active_determination_period_entry = ttk.Entry(main_frame)
active_determination_period_entry.insert(0, "1494554822450")
active_determination_period_entry.grid(column=1, row=9, sticky=(tk.W, tk.E), pady=5)

validity_label = ttk.Label(main_frame, text="Validity")
validity_label.grid(column=0, row=10, sticky=tk.W, pady=5)
validity_entry = ttk.Entry(main_frame)
validity_entry.insert(0, "Vld")
validity_entry.grid(column=1, row=10, sticky=(tk.W, tk.E), pady=5)

activation_state_label = ttk.Label(main_frame, text="Activation State")
activation_state_label.grid(column=0, row=11, sticky=tk.W, pady=5)
activation_state_entry = ttk.Entry(main_frame)
activation_state_entry.insert(0, "On")
activation_state_entry.grid(column=1, row=11, sticky=(tk.W, tk.E), pady=5)

metrics_frame = ttk.Frame(main_frame)
metrics_frame.grid(column=0, row=12, columnspan=2, pady=10)

spo2_var = tk.StringVar(value="SpO2: N/A")
ecg_var = tk.StringVar(value="ECG: N/A")
bp_var = tk.StringVar(value="BP: N/A")

ecg_label = ttk.Label(metrics_frame, text="ECG:")
ecg_label.grid(column=0, row=0, sticky=tk.W, pady=5)
ecg_value_label = ttk.Label(metrics_frame, textvariable=ecg_var)
ecg_value_label.grid(column=1, row=0, sticky=(tk.W), pady=5)

spo2_label = ttk.Label(metrics_frame, text="SpO2:")
spo2_label.grid(column=0, row=1, sticky=tk.W, pady=5)
spo2_value_label = ttk.Label(metrics_frame, textvariable=spo2_var)
spo2_value_label.grid(column=1, row=1, sticky=(tk.W), pady=5)

bp_label = ttk.Label(metrics_frame, text="Blood Pressure (Systolic):")
bp_label.grid(column=0, row=2, sticky=tk.W, pady=5)
bp_value_label = ttk.Label(metrics_frame, textvariable=bp_var)
bp_value_label.grid(column=1, row=2, sticky=(tk.W), pady=5)

status_label = ttk.Label(main_frame, text="Status: Stopped", foreground='red')
status_label.grid(column=0, row=13, columnspan=2, sticky=(tk.W), pady=5)

button_frame = ttk.Frame(main_frame)
button_frame.grid(column=0, row=14, columnspan=2, pady=10)

start_button = ttk.Button(button_frame, text="Start", command=on_start_button)
start_button.pack(side=tk.LEFT, padx=5)

stop_button = ttk.Button(button_frame, text="Stop", command=on_stop_button)
stop_button.pack(side=tk.LEFT, padx=5)

console_label = ttk.Label(main_frame, text="Console Log")
console_label.grid(column=0, row=15, columnspan=2, sticky=tk.W, pady=5)

console_output = scrolledtext.ScrolledText(main_frame, state='disabled', width=100, height=10)
console_output.grid(column=0, row=16, columnspan=2, sticky=(tk.W, tk.E), pady=5)

# Simulate Attack Tab
attack_label = ttk.Label(attack_frame, text="Select Handle for Attack")
attack_label.grid(column=0, row=0, sticky=tk.W, pady=5)

handles = ["numeric.ch0.vmd0", "numeric.ch0.vmd1", "numeric.ch1.vmd0"]
selected_handle_var = tk.StringVar()
handle_dropdown = ttk.Combobox(attack_frame, textvariable=selected_handle_var, values=handles)
handle_dropdown.grid(column=1, row=0, sticky=(tk.W, tk.E), pady=5)
selected_handle_var.set("numeric.ch0.vmd0")  # default selection

attack_value_label = ttk.Label(attack_frame, text="Attack Value")
attack_value_label.grid(column=0, row=1, sticky=tk.W, pady=5)
attack_value_entry = ttk.Entry(attack_frame)
attack_value_entry.insert(0, "50")  # default value
attack_value_entry.grid(column=1, row=1, sticky=(tk.W, tk.E), pady=5)

start_attack_button = ttk.Button(attack_frame, text="Start Attack", command=lambda: start_attack(selected_handle_var.get(), attack_value_entry.get()))
start_attack_button.grid(column=0, row=2, pady=5)
stop_attack_button = ttk.Button(attack_frame, text="Stop Attack", command=stop_attack)
stop_attack_button.grid(column=1, row=2, pady=5)

attack_status_label = ttk.Label(attack_frame, text="Attack Status: Inactive", foreground='red')
attack_status_label.grid(column=0, row=3, columnspan=2, sticky=(tk.W), pady=5)

attack_console_label = ttk.Label(attack_frame, text="Console Log")
attack_console_label.grid(column=0, row=4, columnspan=2, sticky=tk.W, pady=5)

attack_console_output = scrolledtext.ScrolledText(attack_frame, state='disabled', width=100, height=10)
attack_console_output.grid(column=0, row=5, columnspan=2, sticky=(tk.W, tk.E), pady=5)

root.columnconfigure(0, weight=1)
root.rowconfigure(0, weight=1)
main_frame.columnconfigure(1, weight=1)
attack_frame.columnconfigure(1, weight=1)

root.after(50, update_console)

# Ensure buttons are in the correct state at startup
update_attack_buttons_state()

root.mainloop()