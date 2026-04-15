from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QFrame

from core.packet_parser import Packet

_STATE_NAMES = {
    0: 'IDLE',
    1: 'MONITORING',
    2: 'ARMED',
    3: 'EVENT_DETECTED',
    4: 'RECORDING',
    5: 'COMPLETED',
    6: 'FAULT',
}

_STATE_COLORS = {
    0: '#888888',
    1: '#51cf66',
    2: '#ffd43b',
    3: '#ff6b6b',
    4: '#ff922b',
    5: '#74c0fc',
    6: '#ff4444',
}

_BASE_STYLE = 'font-family: monospace; font-size: 11px;'


class StatusBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)

        line = QFrame(self)
        line.setFrameShape(QFrame.HLine)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(20)

        self.lbl_uab   = self._lbl('Uab: —')
        self.lbl_ubc   = self._lbl('Ubc: —')
        self.lbl_uca   = self._lbl('Uca: —')
        self.lbl_uavg  = self._lbl('Uavg: —')
        self.lbl_unb   = self._lbl('Unb: —')
        self.lbl_freq  = self._lbl('f: —')
        self.lbl_state = self._lbl('IDLE')
        self.lbl_flags = self._lbl('')

        for w in (self.lbl_uab, self.lbl_ubc, self.lbl_uca,
                  self.lbl_uavg, self.lbl_unb, self.lbl_freq,
                  self.lbl_state, self.lbl_flags):
            layout.addWidget(w)

        layout.addStretch()

    @staticmethod
    def _lbl(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(_BASE_STYLE)
        return lbl

    def update_packet(self, p: Packet) -> None:
        self.lbl_uab.setText(f'Uab: {p.uab:.1f} V')
        self.lbl_ubc.setText(f'Ubc: {p.ubc:.1f} V')
        self.lbl_uca.setText(f'Uca: {p.uca:.1f} V')
        self.lbl_uavg.setText(f'Uavg: {p.uavg:.1f} V')
        self.lbl_unb.setText(f'Unb: {p.unb:.2f}%')
        self.lbl_freq.setText(f'f: {p.f:.2f} Hz' if p.f > 0 else 'f: —')

        state_name  = _STATE_NAMES.get(p.state, '?')
        state_color = _STATE_COLORS.get(p.state, '#ffffff')
        self.lbl_state.setText(state_name)
        self.lbl_state.setStyleSheet(
            f'color: {state_color}; font-weight: bold; {_BASE_STYLE}'
        )

        self.lbl_flags.setText('  '.join(f'[{f}]' for f in p.flags))
