"""
Filename: <consumer_GUI.py>
Description: <provides a graphical user interface (GUI) for both an SDC (Service-Oriented Device Connectivity) consumer>

Author: <Kevin Wollowski>
Company: <if(is)>
Email: <wollowski@internet-sicherheit.de>
Date: 25.06.2024>
Version: <0.3>

License: <MIT License>
"""



import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import queue
import re
import time
import uuid
from datetime import datetime, timedelta
from sdc11073 import pmtypes
from sdc11073.namespaces import domTag
from sdc11073.wsdiscovery import WSDiscoverySingleAdapter
from sdc11073.definitions_sdc import SDC_v1_Definitions
from sdc11073.sdcclient import SdcClient
from sdc11073.mdib import ClientMdibContainer
from sdc11073 import observableproperties
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.dates import DateFormatter

try:
    from PIL import Image, ImageTk
except ImportError:
    from PIL.ImageTk import Image, ImageTk

class SDCGui:
    def __init__(self, root):
        """ Initialize the SDCGui class, set up the main window and data structures. """
        self.root = root
        self.root.title("SDC Client")
        self.data_queue = queue.Queue()  # Queue for storing data updates
        self.create_widgets()  # Initialize GUI widgets
        # Data storage for three different plots
        self.data1 = {'time': [], 'value': []}
        self.data2 = {'time': [], 'value': []}
        self.data3 = {'time': [], 'value': []}
        self.running = False  # Flag to control the running state of the client
        self.repaint_needed = False  # Flag to indicate if the plots need repainting
        self.plot_minutes = 2  # Number of minutes of data to be plotted by default
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)  # Handle window close event

    def on_closing(self):
        """ Handle the window close event to clean up resources and stop the client. """
        if self.running:
            self.running = False
            if hasattr(self, 'worker_thread'):  # Check if the worker thread exists
                self.worker_thread.join()  # Wait for the thread to finish
        self.root.quit()  # Stop the mainloop
        self.root.destroy()  # Close the Tkinter window

    def create_widgets(self):
        """ Create and layout the GUI components. """
        self.root.geometry("900x700")  # Set window size
        self.root.minsize(700, 500)  # Set minimum window size

        self.notebook = ttk.Notebook(self.root)  # Create a notebook for tabs
        self.notebook.pack(expand=1, fill="both")

        self.client_info_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.client_info_frame, text="Client Info")  # Add "Client Info" tab

        # Frame for displaying client information
        info_frame = ttk.LabelFrame(self.client_info_frame, text="SDC Client Information")
        info_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")

        # Labels for displaying UUID, IP Address, Type, and Client Subscriber Info
        ttk.Label(info_frame, text="UUID:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.uuid_label = ttk.Label(info_frame, text="")
        self.uuid_label.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)

        ttk.Label(info_frame, text="IP Address:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.ip_label = ttk.Label(info_frame, text="")
        self.ip_label.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)

        ttk.Label(info_frame, text="Type:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        self.type_label = ttk.Label(info_frame, text="")
        self.type_label.grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)

        ttk.Label(info_frame, text="Client Subscriber Info:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=5)
        self.client_subscriber_info = ttk.Label(info_frame, text="")
        self.client_subscriber_info.grid(row=3, column=1, sticky=tk.W, padx=5, pady=5)

        # Frame for data visualization
        self.data_visualizer_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.data_visualizer_frame, text="Data Visualizer")  # Add "Data Visualizer" tab

        # Set up matplotlib figures and axes for plotting data
        self.fig, (self.ax1, self.ax2, self.ax3) = plt.subplots(3, 1, figsize=(7, 6), dpi=100, sharex=True)
        self.fig.subplots_adjust(hspace=0.5)
        self.ax1.set_title("Cardiac Activity (ECG)")
        self.ax2.set_title("Sp02")
        self.ax3.set_title("Bloodpressure")
        self.ax1.set_ylabel("Value")
        self.ax2.set_ylabel("Value")
        self.ax3.set_ylabel("Value")
        self.ax3.set_xlabel("Time")

        # Set date formatter for x-axis
        self.ax1.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))
        self.ax2.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))
        self.ax3.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))

        # Enable grid and legends
        self.ax1.grid(True)
        self.ax2.grid(True)
        self.ax3.grid(True)
        self.ax1.legend(["Value"], loc="upper right")
        self.ax2.legend(["Value"], loc="upper right")
        self.ax3.legend(["Value"], loc="upper right")

        # Create a canvas for the matplotlib figure
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.data_visualizer_frame)
        self.canvas.get_tk_widget().pack(expand=1, fill="both")

        # Bottom frame for control buttons and status label
        bottom_frame = ttk.Frame(self.root)
        bottom_frame.pack(side=tk.BOTTOM, fill="x", expand=False, padx=10, pady=10)

        # Frame for plot settings
        plot_settings_frame = ttk.Frame(bottom_frame)
        plot_settings_frame.pack(side=tk.TOP, fill="x", expand=False)

        ttk.Label(plot_settings_frame, text="Plot last minutes of data:").pack(side=tk.LEFT, padx=5)
        self.plot_minutes_var = tk.StringVar(value="2")  # Default value is 2 minutes
        self.plot_minutes_entry = ttk.Entry(plot_settings_frame, textvariable=self.plot_minutes_var, width=5)
        self.plot_minutes_entry.pack(side=tk.LEFT, padx=5)

        self.save_button = ttk.Button(plot_settings_frame, text="Save", command=self.save_plot_settings)
        self.save_button.pack(side=tk.LEFT, padx=5)

        # Frame for control buttons
        control_frame = ttk.Frame(bottom_frame)
        control_frame.pack(side=tk.TOP, fill="x", expand=False)

        # Run and Stop buttons
        self.run_button = ttk.Button(control_frame, text="Run", command=self.run_sdc_client)
        self.run_button.pack(side=tk.LEFT, padx=5)

        self.stop_button = ttk.Button(control_frame, text="Stop", command=self.stop_sdc_client, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)

        # Status label to show the running status of the client
        self.status_label = ttk.Label(control_frame, text="Status: Stopped", foreground="red")
        self.status_label.pack(side=tk.LEFT, padx=10)

        # Frame for updates console
        self.updates_frame = ttk.Frame(bottom_frame)
        self.updates_frame.pack(side=tk.BOTTOM, fill="x", expand=False)

        updates_label = ttk.Label(self.updates_frame, text="Updates Console:")
        updates_label.pack(anchor="w")

        self.updates_text = scrolledtext.ScrolledText(self.updates_frame, height=10, wrap=tk.WORD)
        self.updates_text.pack(fill="both", expand=True, padx=5, pady=5)

        # Configure column and row weights for resizing
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        self.client_info_frame.grid_columnconfigure(0, weight=1)
        self.client_info_frame.grid_rowconfigure(0, weight=1)
        self.data_visualizer_frame.grid_columnconfigure(0, weight=1)
        self.data_visualizer_frame.grid_rowconfigure(0, weight=1)

    def save_plot_settings(self):
        """ Save the plot settings and log the change. """
        try:
            self.plot_minutes = int(self.plot_minutes_var.get())
            self.add_update_message(f"Updated plot duration to the last {self.plot_minutes} minutes.")
        except ValueError:
            self.add_update_message(f"Invalid input for plot duration. Please enter a valid number.")

    def run_sdc_client(self):
        """ Start the SDC client service in a separate thread. """
        if not self.running:
            self.status_label.config(text="Status: Running", foreground="green")
            self.run_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL)
            self.clear_updates_log()
            self.clear_plots()
            self.clear_labels()
            self.running = True
            self.worker_thread = threading.Thread(target=self.sdc_client_process)
            self.worker_thread.daemon = True  # Set thread as daemon
            self.worker_thread.start()
            self.root.after(100, self.update_gui)  # Schedule GUI updates

    def stop_sdc_client(self):
        """ Stop the SDC client service and update the GUI. """
        if self.running:
            self.running = False
            self.worker_thread.join()  # Wait for the thread to finish
            self.status_label.config(text="Status: Stopped", foreground="red")
            self.run_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            stop_message = f"{datetime.now().strftime('%d:%m:%Y %H:%M:%S.%f')[:-3]} - Client stopped"
            self.add_update_message(stop_message)
            self.clear_labels()

    def clear_labels(self):
        """ Clear the client info labels. """
        self.uuid_label.config(text="")
        self.ip_label.config(text="")
        self.type_label.config(text="")
        self.client_subscriber_info.config(text="")

    def update_gui(self):
        """ Update the GUI with data from the queue. """
        try:
            start_time = time.time()  # Start time for the update loop
            updates_processed = 0
            while not self.data_queue.empty():
                update = self.data_queue.get_nowait()  # Non-blocking call
                if update.get("type") == "info":
                    self.update_gui_with_service_info(update)  # Update service info
                elif update.get("type") == "metric":
                    self.update_gui_with_text(update["text"])  # Update text in console
                    self.update_plot(update["handle"], update["value"])  # Update plot with new data
                    updates_processed += 1
                    if updates_processed > 10:
                        break  # Avoid too many updates at once
        except Exception as e:
            self.add_update_message(f"Error updating GUI: {str(e)}")
        finally:
            if self.repaint_needed:
                self.canvas.draw()  # Repaint the plots
                self.repaint_needed = False
            if self.running:
                elapsed_time = time.time() - start_time
                next_update = max(100, int(100 - (elapsed_time * 1000)))  # Ensure at least 100ms interval
                self.root.after(next_update, self.update_gui)

    def update_gui_with_service_info(self, info):
        """ Update the GUI with service information. """
        self.uuid_label.config(text=info["uuid"])
        self.ip_label.config(text=info["ip"])
        self.type_label.config(text=info["types"])
        self.client_subscriber_info.config(text=info["subscriber_info"])

    def update_gui_with_text(self, text):
        """ Add a text update to the console log. """
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.add_update_message(f"{timestamp} - {text}")

    def add_update_message(self, message):
        """ Add a message to the updates console. """
        self.updates_text.insert(tk.END, message + "\n")
        self.updates_text.see(tk.END)

    def clear_updates_log(self):
        """ Clear the updates console log. """
        self.updates_text.delete(1.0, tk.END)

    def clear_plots(self):
        """ Clear the data plots and reset axes. """
        self.ax1.clear()
        self.ax2.clear()
        self.ax3.clear()
        self.data1 = {'time': [], 'value': []}
        self.data2 = {'time': [], 'value': []}
        self.data3 = {'time': [], 'value': []}
        self.ax1.set_title("Cardiac Activity (ECG)")
        self.ax2.set_title("Sp02")
        self.ax3.set_title("Bloodpressure")
        self.ax1.set_ylabel("Value")
        self.ax2.set_ylabel("Value")
        self.ax3.set_ylabel("Value")
        self.ax3.set_xlabel("Time")
        self.ax1.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))
        self.ax2.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))
        self.ax3.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))
        self.ax1.grid(True)
        self.ax2.grid(True)
        self.ax3.grid(True)
        self.ax1.legend(["Current Value"], loc="upper right")
        self.ax2.legend(["Current Value"], loc="upper right")
        self.ax3.legend(["Current Value"], loc="upper right")
        self.fig.tight_layout()
        self.canvas.draw()

    def update_plot(self, handle, value):
        """ Update the plot with new data based on the metric handle. """
        try:
            value = float(value)
            current_time = datetime.now()
            value_str = f"{value:.2f}"  # Format value to 2 decimal places

            # Append data and remove old data based on the configured plot duration
            if "numeric.ch0.vmd0" in handle:
                self.data1['time'].append(current_time)
                self.data1['value'].append(value)
                self.data1['time'] = [t for t in self.data1['time'] if (current_time - t).total_seconds() < self.plot_minutes * 60]
                self.data1['value'] = self.data1['value'][-len(self.data1['time']):]
                self.ax1.clear()
                self.ax1.set_title(f"Cardiac Activity (ECG)")
                self.ax1.set_ylabel("Value")
                self.ax1.plot(self.data1['time'], self.data1['value'], 'b-', label=f"Current Value: {value_str}")
                self.ax1.legend(loc="upper right")
            elif "numeric.ch0.vmd1" in handle:
                self.data2['time'].append(current_time)
                self.data2['value'].append(value)
                self.data2['time'] = [t for t in self.data2['time'] if (current_time - t).total_seconds() < self.plot_minutes * 60]
                self.data2['value'] = self.data2['value'][-len(self.data2['time']):]
                self.ax2.clear()
                self.ax2.set_title(f"Sp02")
                self.ax2.set_ylabel("Value")
                self.ax2.plot(self.data2['time'], self.data2['value'], 'r-', label=f"Current Value: {value_str}")
                self.ax2.legend(loc="upper right")
            elif "numeric.ch1.vmd0" in handle:
                self.data3['time'].append(current_time)
                self.data3['value'].append(value)
                self.data3['time'] = [t for t in self.data3['time'] if (current_time - t).total_seconds() < self.plot_minutes * 60]
                self.data3['value'] = self.data3['value'][-len(self.data3['time']):]
                self.ax3.clear()
                self.ax3.set_title(f"Bloodpressure")
                self.ax3.set_ylabel("Value")
                self.ax3.plot(self.data3['time'], self.data3['value'], 'g-', label=f"Current Value: {value_str}")
                self.ax3.legend(loc="upper right")

            self.repaint_needed = True  # Flag repaint needed
            self.fig.tightlayout()
        except ValueError:
            pass


    def sdc_client_process(self):
        """ SDC client process to handle discovery and data retrieval. """
        baseUUID = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050230}')
        device_A_UUID = uuid.uuid5(baseUUID, "12345")

        def onMetricUpdate(metricsByHandle):
            """ Callback function for metric updates. """
            for handle in metricsByHandle:
                metric_state = metricsByHandle[handle]
                value = metric_state.metricValue.Value if metric_state.metricValue else "No Value"
                update_text = f"Got update on: {handle}, Value: {value}"
                self.data_queue.put({"type": "metric", "text": update_text, "handle": handle, "value": value})

        def setEnsembleContext(theMDIB, theClient):
            """ Set ensemble context for the MDIB. """
            descriptorContainer = theMDIB.descriptions.NODETYPE.getOne(domTag('EnsembleContextDescriptor'))
            contextClient = theClient.ContextService_client
            operationHandle = None
            for oneOp in theMDIB.descriptions.NODETYPE.get(domTag('SetContextStateOperationDescriptor'), []):
                if oneOp.OperationTarget == descriptorContainer.handle:
                    operationHandle = oneOp.Handle
            newEnsembleContext = contextClient.mkProposedContextObject(descriptorContainer.handle)
            newEnsembleContext.ContextAssociation = 'Assoc'
            newEnsembleContext.Identification = [
                pmtypes.InstanceIdentifier(root="1.2.3", extensionString="SupervisorSuperEnsemble")]
            contextClient.setContextState(operationHandle, [newEnsembleContext])

        myDiscovery = WSDiscoverySingleAdapter("Ethernet")  # Set up WS-Discovery
        myDiscovery.start()
        foundDevice = False
        try:
            while not foundDevice and self.running:
                services = myDiscovery.searchServices(types=SDC_v1_Definitions.MedicalDeviceTypesFilter)
                for oneService in services:
                    try:
                        if oneService.getEPR() == device_A_UUID.urn:
                            my_client = SdcClient.fromWsdService(oneService)
                            my_client.startAll()
                            my_mdib = ClientMdibContainer(my_client)
                            my_mdib.initMdib()
                            observableproperties.bind(my_mdib, metricsByHandle=onMetricUpdate)

                            # Extract and display service information
                            uuid_str = oneService.getEPR()
                            xaddr = oneService.getXAddrs()[0]
                            ip_match = re.search(r'http://(\d+\.\d+\.\d+\.\d+):', xaddr)
                            ip = ip_match.group(1) if ip_match else "Unknown IP"
                            types = ', '.join([str(t) for t in oneService.getTypes()])
                            subscriber_info = oneService.getXAddrs()[0]

                            self.data_queue.put({
                                "type": "info",
                                "uuid": uuid_str,
                                "ip": ip,
                                "types": types,
                                "subscriber_info": subscriber_info
                            })

                            setEnsembleContext(my_mdib, my_client)
                            foundDevice = True
                            break
                    except Exception as e:
                        print(f"Problem in discovery, ignoring it: {e}")
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            myDiscovery.stop()

def main():
    """ Main function to run the SDCGui application. """
    root = tk.Tk()
    app = SDCGui(root)
    root.mainloop()
    if app.running:
        app.running = False
        app.worker_thread.join()

if __name__ == "__main__":
    main()
