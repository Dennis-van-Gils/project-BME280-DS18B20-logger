#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BME280 & DS18B20 logger
"""
__author__ = "Dennis van Gils"
__authoremail__ = "vangils.dennis@gmail.com"
__url__ = "https://github.com/Dennis-van-Gils/....."
__date__ = "29-07-2020"
__version__ = "1.0"
# pylint: disable=bare-except, broad-except

import os
import sys
import time

import numpy as np
import psutil

from PyQt5 import QtCore, QtGui
from PyQt5 import QtWidgets as QtWid
from PyQt5.QtCore import QDateTime
import pyqtgraph as pg

from dvg_debug_functions import tprint, dprint, print_fancy_traceback as pft
from dvg_devices.Arduino_protocol_serial import Arduino
from dvg_qdeviceio import QDeviceIO

from dvg_pyqtgraph_threadsafe import ThreadSafeGraphicsWindow

from DvG_pyqt_FileLogger import FileLogger
from DvG_pyqt_controls import create_Toggle_button, SS_GROUP

try:
    import OpenGL.GL as gl  # pylint: disable=unused-import
except:
    print("OpenGL acceleration: Disabled")
    print("To install: `conda install pyopengl` or `pip install pyopengl`")
else:
    print("OpenGL acceleration: Enabled")
    pg.setConfigOptions(useOpenGL=True)
    pg.setConfigOptions(antialias=True)
    pg.setConfigOptions(enableExperimental=True)

# Global pyqtgraph configuration
pg.setConfigOptions(leftButtonPan=False)
pg.setConfigOption("foreground", "#EEE")

# Constants
# fmt: off
DAQ_INTERVAL_MS    = 1000  # [ms]
CHART_INTERVAL_MS  = 500   # [ms]
CHART_HISTORY_TIME = 60    # [s]
# fmt: on

# Show debug info in terminal? Warning: Slow! Do not leave on unintentionally.
DEBUG = False


def get_current_date_time():
    cur_date_time = QDateTime.currentDateTime()
    return (
        cur_date_time.toString("dd-MM-yyyy"),  # Date
        cur_date_time.toString("HH:mm:ss"),  # Time
        cur_date_time.toString("yyMMdd_HHmmss"),  # Reverse notation date-time
    )


# ------------------------------------------------------------------------------
#   Arduino state
# ------------------------------------------------------------------------------


class State(object):
    """Reflects the actual readings, parsed into separate variables, of the
    Arduino. There should only be one instance of the State class.
    """

    def __init__(self):
        self.time = np.nan  # [s]
        self.ds_temp = np.nan  # ['C]
        self.bme_temp = np.nan  # ['C]
        self.bme_humi = np.nan  # [%]
        self.bme_pres = np.nan  # [bar]


state = State()

# ------------------------------------------------------------------------------
#   MainWindow
# ------------------------------------------------------------------------------


class MainWindow(QtWid.QWidget):
    def __init__(self, parent=None, **kwargs):
        super().__init__(parent, **kwargs)

        self.setWindowTitle("Temperature-humidity-pressure logger")
        self.setGeometry(350, 50, 800, 800)

        # -------------------------
        #   Top frame
        # -------------------------

        # Left box
        self.qlbl_update_counter = QtWid.QLabel("0")
        self.qlbl_DAQ_rate = QtWid.QLabel("DAQ: 0 Hz")
        self.qlbl_DAQ_rate.setMinimumWidth(100)

        vbox_left = QtWid.QVBoxLayout()
        vbox_left.addWidget(self.qlbl_update_counter, stretch=0)
        vbox_left.addStretch(1)
        vbox_left.addWidget(self.qlbl_DAQ_rate, stretch=0)

        # Middle box
        self.qlbl_title = QtWid.QLabel(
            "Uber-Monitor",
            font=QtGui.QFont("Palatino", 14, weight=QtGui.QFont.Bold),
        )
        self.qlbl_title.setAlignment(QtCore.Qt.AlignCenter)
        self.qlbl_cur_date_time = QtWid.QLabel("00-00-0000    00:00:00")
        self.qlbl_cur_date_time.setAlignment(QtCore.Qt.AlignCenter)
        self.qpbt_record = create_Toggle_button(
            "Click to start recording to file", minimumHeight=40,
        )
        self.qpbt_record.clicked.connect(self.process_qpbt_record)

        vbox_middle = QtWid.QVBoxLayout()
        vbox_middle.addWidget(self.qlbl_title)
        vbox_middle.addWidget(self.qlbl_cur_date_time)
        vbox_middle.addWidget(self.qpbt_record)

        # Right box
        self.qpbt_exit = QtWid.QPushButton("Exit")
        self.qpbt_exit.clicked.connect(self.close)
        self.qpbt_exit.setMinimumHeight(30)

        vbox_right = QtWid.QVBoxLayout()
        vbox_right.addWidget(self.qpbt_exit, stretch=0)
        vbox_right.addStretch(1)

        # Round up top frame
        hbox_top = QtWid.QHBoxLayout()
        hbox_top.addLayout(vbox_left, stretch=0)
        hbox_top.addStretch(1)
        hbox_top.addLayout(vbox_middle, stretch=0)
        hbox_top.addStretch(1)
        hbox_top.addLayout(vbox_right, stretch=0)

        # -------------------------
        #   Bottom frame
        # -------------------------

        # Chart
        num_samples = round(CHART_HISTORY_TIME * 1e3 / DAQ_INTERVAL_MS)

        PEN_01 = pg.mkPen(color=[255, 255, 0], width=3)
        PEN_02 = pg.mkPen(color=[0, 255, 255], width=3)

        self.tsgwin = ThreadSafeGraphicsWindow()
        self.tsgwin.setBackground([20, 20, 20])

        # Plot: Temperatures
        p = {"color": "#EEE", "font-size": "10pt"}
        self.tsplot_temp = self.tsgwin.addThreadSafePlot(row=0, col=0)
        self.tsplot_temp.showGrid(x=1, y=1)
        self.tsplot_temp.setLabel("bottom", text="history (s)", **p)
        self.tsplot_temp.setLabel("left", text="temperature ('C)", **p)
        self.tsplot_temp.setRange(
            xRange=[-1.04 * CHART_HISTORY_TIME, CHART_HISTORY_TIME * 0.04],
            yRange=[18, 30],
            disableAutoRange=True,
        )
        self.tscurve_ds_temp = self.tsgwin.addThreadSafeCurve(
            "HistoryChartCurve", capacity=num_samples, pen=PEN_01,
        )
        self.tscurve_bme_temp = self.tsgwin.addThreadSafeCurve(
            "HistoryChartCurve", capacity=num_samples, pen=PEN_02,
        )

        # Plot: Humidity
        p = {"color": "#EEE", "font-size": "10pt"}
        self.tsplot_humi = self.tsgwin.addThreadSafePlot(row=1, col=0)
        self.tsplot_humi.showGrid(x=1, y=1)
        self.tsplot_humi.setLabel("bottom", text="history (s)", **p)
        self.tsplot_humi.setLabel("left", text="humidity (%)", **p)
        self.tsplot_humi.setRange(
            xRange=[-1.04 * CHART_HISTORY_TIME, CHART_HISTORY_TIME * 0.04],
            yRange=[0, 100],
            disableAutoRange=True,
        )
        self.tscurve_bme_humi = self.tsgwin.addThreadSafeCurve(
            "HistoryChartCurve", capacity=num_samples, pen=PEN_02,
        )

        # Plot: Pressure
        p = {"color": "#EEE", "font-size": "10pt"}
        self.tsplot_pres = self.tsgwin.addThreadSafePlot(row=2, col=0)
        self.tsplot_pres.showGrid(x=1, y=1)
        self.tsplot_pres.setLabel("bottom", text="history (s)", **p)
        self.tsplot_pres.setLabel("left", text="pressure (bar)", **p)
        self.tsplot_pres.setRange(
            xRange=[-1.04 * CHART_HISTORY_TIME, CHART_HISTORY_TIME * 0.04],
            yRange=[0.9, 1.2],
            disableAutoRange=True,
        )
        self.tscurve_bme_pres = self.tsgwin.addThreadSafeCurve(
            "HistoryChartCurve", capacity=num_samples, pen=PEN_02,
        )

        # 'Readings'
        p = {
            "readOnly": True,
            "alignment": QtCore.Qt.AlignRight,
            "maximumWidth": 50,
        }
        self.qlin_reading_time = QtWid.QLineEdit(**p)
        self.qlin_reading_ds_temp = QtWid.QLineEdit(**p)
        self.qlin_reading_bme_temp = QtWid.QLineEdit(**p)
        self.qlin_reading_bme_humi = QtWid.QLineEdit(**p)
        self.qlin_reading_bme_pres = QtWid.QLineEdit(**p)

        # fmt: off
        grid = QtWid.QGridLayout()
        grid.addWidget(QtWid.QLabel("time")      , 0, 0)
        grid.addWidget(self.qlin_reading_time    , 0, 1)
        grid.addWidget(QtWid.QLabel("s")         , 0, 2)
        grid.addWidget(QtWid.QLabel("DS temp")   , 1, 0)
        grid.addWidget(self.qlin_reading_ds_temp , 1, 1)
        grid.addWidget(QtWid.QLabel("'C")        , 1, 2)
        grid.addWidget(QtWid.QLabel("BME temp")  , 2, 0)
        grid.addWidget(self.qlin_reading_bme_temp, 2, 1)
        grid.addWidget(QtWid.QLabel("'C")        , 2, 2)
        grid.addWidget(QtWid.QLabel("BME humi")  , 3, 0)
        grid.addWidget(self.qlin_reading_bme_humi, 3, 1)
        grid.addWidget(QtWid.QLabel("%")         , 3, 2)
        grid.addWidget(QtWid.QLabel("BME pres")  , 4, 0)
        grid.addWidget(self.qlin_reading_bme_pres, 4, 1)
        grid.addWidget(QtWid.QLabel("bar")       , 4, 2)
        grid.setAlignment(QtCore.Qt.AlignTop)
        # fmt: on

        qgrp_readings = QtWid.QGroupBox("Readings")
        qgrp_readings.setStyleSheet(SS_GROUP)
        qgrp_readings.setLayout(grid)

        # 'Chart'
        self.qpbt_clear_chart = QtWid.QPushButton("Clear")
        self.qpbt_clear_chart.clicked.connect(self.process_qpbt_clear_chart)

        grid = QtWid.QGridLayout()
        grid.addWidget(self.qpbt_clear_chart, 0, 0)
        grid.setAlignment(QtCore.Qt.AlignTop)

        qgrp_chart = QtWid.QGroupBox("Chart")
        qgrp_chart.setStyleSheet(SS_GROUP)
        qgrp_chart.setLayout(grid)

        vbox = QtWid.QVBoxLayout()
        vbox.addWidget(qgrp_readings)
        vbox.addWidget(qgrp_chart)
        vbox.addStretch()

        # Round up bottom frame
        hbox_bot = QtWid.QHBoxLayout()
        hbox_bot.addWidget(self.tsgwin, 1)
        hbox_bot.addLayout(vbox, 0)

        # -------------------------
        #   Round up full window
        # -------------------------

        vbox = QtWid.QVBoxLayout(self)
        vbox.addLayout(hbox_top, stretch=0)
        vbox.addSpacerItem(QtWid.QSpacerItem(0, 20))
        vbox.addLayout(hbox_bot, stretch=1)

    # --------------------------------------------------------------------------
    #   Handle controls
    # --------------------------------------------------------------------------

    @QtCore.pyqtSlot()
    def process_qpbt_clear_chart(self):
        str_msg = "Are you sure you want to clear the chart?"
        reply = QtWid.QMessageBox.warning(
            window,
            "Clear chart",
            str_msg,
            QtWid.QMessageBox.Yes | QtWid.QMessageBox.No,
            QtWid.QMessageBox.No,
        )

        if reply == QtWid.QMessageBox.Yes:
            self.tsgwin.clear_curves()

    @QtCore.pyqtSlot()
    def process_qpbt_record(self):
        if self.qpbt_record.isChecked():
            file_logger.starting = True
        else:
            file_logger.stopping = True

    @QtCore.pyqtSlot(str)
    def set_text_qpbt_record(self, text_str):
        self.qpbt_record.setText(text_str)


# ------------------------------------------------------------------------------
#   update_GUI
# ------------------------------------------------------------------------------


@QtCore.pyqtSlot()
def update_GUI():
    str_cur_date, str_cur_time, _ = get_current_date_time()
    window.qlbl_cur_date_time.setText("%s    %s" % (str_cur_date, str_cur_time))
    window.qlbl_update_counter.setText("%i" % qdev_ard.update_counter_DAQ)
    window.qlbl_DAQ_rate.setText("DAQ: %.1f Hz" % qdev_ard.obtained_DAQ_rate_Hz)

    window.qlin_reading_time.setText("%.1f" % state.time)
    window.qlin_reading_ds_temp.setText("%.1f" % state.ds_temp)
    window.qlin_reading_bme_temp.setText("%.1f" % state.bme_temp)
    window.qlin_reading_bme_humi.setText("%.1f" % state.bme_humi)
    window.qlin_reading_bme_pres.setText("%.3f" % state.bme_pres)


# ------------------------------------------------------------------------------
#   update_chart
# ------------------------------------------------------------------------------


@QtCore.pyqtSlot()
def update_chart():
    if DEBUG:
        tprint("update_curve")

    window.tsgwin.update_curves()


# ------------------------------------------------------------------------------
#   Program termination routines
# ------------------------------------------------------------------------------


def stop_running():
    app.processEvents()
    qdev_ard.quit()
    file_logger.close_log()

    print("Stopping timers: ", end="")
    timer_chart.stop()
    print("done.")


@QtCore.pyqtSlot()
def notify_connection_lost():
    stop_running()

    window.qlbl_title.setText("    ! ! !    LOST CONNECTION    ! ! !    ")
    str_cur_date, str_cur_time, _ = get_current_date_time()
    str_msg = "%s %s\nLost connection to Arduino." % (
        str_cur_date,
        str_cur_time,
    )
    print("\nCRITICAL ERROR @ %s" % str_msg)
    reply = QtWid.QMessageBox.warning(
        window, "CRITICAL ERROR", str_msg, QtWid.QMessageBox.Ok
    )

    if reply == QtWid.QMessageBox.Ok:
        pass  # Leave the GUI open for read-only inspection by the user


@QtCore.pyqtSlot()
def about_to_quit():
    print("\nAbout to quit")
    stop_running()
    ard.close()


# ------------------------------------------------------------------------------
#   Your Arduino update function
# ------------------------------------------------------------------------------


def DAQ_function():
    # Date-time keeping
    str_cur_date, str_cur_time, str_cur_datetime = get_current_date_time()

    # Query the Arduino for its state
    success, tmp_state = ard.query_ascii_values("?", delimiter="\t")
    if not (success):
        dprint(
            "'%s' reports IOError @ %s %s"
            % (ard.name, str_cur_date, str_cur_time)
        )
        return False

    # Parse readings into separate state variables
    try:
        (
            state.time,
            state.ds_temp,
            state.bme_temp,
            state.bme_humi,
            state.bme_pres,
        ) = tmp_state
        state.time /= 1000
        state.bme_pres /= 1e5
    except Exception as err:
        pft(err, 3)
        dprint(
            "'%s' reports IOError @ %s %s"
            % (ard.name, str_cur_date, str_cur_time)
        )
        return False

    # Use Arduino time or PC time?
    use_PC_time = True
    if use_PC_time:
        state.time = time.perf_counter()

    # Add readings to chart histories
    window.tscurve_ds_temp.add_new_reading(state.time, state.ds_temp)
    window.tscurve_bme_temp.add_new_reading(state.time, state.bme_temp)
    window.tscurve_bme_humi.add_new_reading(state.time, state.bme_humi)
    window.tscurve_bme_pres.add_new_reading(state.time, state.bme_pres)

    # Logging to file
    if file_logger.starting:
        fn_log = str_cur_datetime + ".txt"
        if file_logger.create_log(state.time, fn_log, mode="w"):
            file_logger.signal_set_recording_text.emit(
                "Recording to file: " + fn_log
            )
            file_logger.write(
                "time [s]\t"
                "DS18B20 temp ('C)\t"
                "BME280 temp ('C)\t"
                "BME280 humi (pct)\t"
                "BME280 pres (bar)\n"
            )

    if file_logger.stopping:
        file_logger.signal_set_recording_text.emit(
            "Click to start recording to file"
        )
        file_logger.close_log()

    if file_logger.is_recording:
        log_elapsed_time = state.time - file_logger.start_time
        file_logger.write(
            "%.1f\t%.3f\t%.3f\t%.3f\t%.5f\n"
            % (
                log_elapsed_time,
                state.ds_temp,
                state.bme_temp,
                state.bme_humi,
                state.bme_pres,
            )
        )

    return True


# ------------------------------------------------------------------------------
#   Main
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    # Set priority of this process to maximum in the operating system
    print("PID: %s\n" % os.getpid())
    try:
        proc = psutil.Process(os.getpid())
        if os.name == "nt":
            proc.nice(psutil.REALTIME_PRIORITY_CLASS)  # Windows
        else:
            proc.nice(-20)  # Other
    except:
        print("Warning: Could not set process to maximum priority.\n")

    # --------------------------------------------------------------------------
    #   Connect to Arduino
    # --------------------------------------------------------------------------

    ard = Arduino(name="Ard", connect_to_specific_ID="BME280 & DS18B20 logger")

    ard.serial_settings["baudrate"] = 115200
    ard.auto_connect("last_used_port.txt")

    if not (ard.is_alive):
        print("\nCheck connection and try resetting the Arduino.")
        print("Exiting...\n")
        sys.exit(0)

    # --------------------------------------------------------------------------
    #   Create application and main window
    # --------------------------------------------------------------------------
    QtCore.QThread.currentThread().setObjectName("MAIN")  # For DEBUG info

    app = 0  # Work-around for kernel crash when using Spyder IDE
    app = QtWid.QApplication(sys.argv)
    app.aboutToQuit.connect(about_to_quit)

    window = MainWindow()

    # --------------------------------------------------------------------------
    #   File logger
    # --------------------------------------------------------------------------

    file_logger = FileLogger()
    file_logger.signal_set_recording_text.connect(window.set_text_qpbt_record)

    # --------------------------------------------------------------------------
    #   Set up multithreaded communication with the Arduino
    # --------------------------------------------------------------------------

    # Create QDeviceIO
    qdev_ard = QDeviceIO(ard)

    # Create workers
    # fmt: off
    qdev_ard.create_worker_DAQ(
        DAQ_function             = DAQ_function,
        DAQ_interval_ms          = DAQ_INTERVAL_MS,
        critical_not_alive_count = 3,
        debug                    = DEBUG,
    )
    # fmt: on

    # Connect signals to slots
    qdev_ard.signal_DAQ_updated.connect(update_GUI)
    qdev_ard.signal_connection_lost.connect(notify_connection_lost)

    # Start workers
    qdev_ard.start(DAQ_priority=QtCore.QThread.TimeCriticalPriority)

    # --------------------------------------------------------------------------
    #   Create chart refresh timer
    # --------------------------------------------------------------------------

    timer_chart = QtCore.QTimer()
    # timer_chart.setTimerType(QtCore.Qt.PreciseTimer)
    timer_chart.timeout.connect(update_chart)
    timer_chart.start(CHART_INTERVAL_MS)

    # --------------------------------------------------------------------------
    #   Start the main GUI event loop
    # --------------------------------------------------------------------------

    window.show()
    sys.exit(app.exec_())
