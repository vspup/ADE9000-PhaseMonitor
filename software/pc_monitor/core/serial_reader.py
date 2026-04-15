import serial
from PySide6.QtCore import QThread, Signal


class SerialReader(QThread):
    """Background thread: opens serial port, emits one signal per received line."""

    line_received      = Signal(str)
    error_occurred     = Signal(str)
    connection_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self._port    = ''
        self._baud    = 115200
        self._running = False

    def configure(self, port: str, baud: int = 115200) -> None:
        self._port = port
        self._baud = baud

    def run(self) -> None:
        ser = None
        try:
            ser = serial.Serial(self._port, self._baud, timeout=1.0)
            self._running = True
            self.connection_changed.emit(True)

            while self._running:
                try:
                    raw = ser.readline()
                except serial.SerialException as e:
                    self.error_occurred.emit(str(e))
                    break

                line = raw.decode('utf-8', errors='ignore').strip()
                if line:
                    self.line_received.emit(line)

        except serial.SerialException as e:
            self.error_occurred.emit(str(e))
        finally:
            if ser and ser.is_open:
                ser.close()
            self._running = False
            self.connection_changed.emit(False)

    def stop(self) -> None:
        self._running = False
        self.wait(2000)
