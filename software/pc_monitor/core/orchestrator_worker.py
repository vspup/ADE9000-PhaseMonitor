"""QThread wrapper for Orchestrator + session_writer.

Runs the full capture sequence in a background thread so the PySide6 UI
stays responsive. Caller supplies client instances; this module handles
signals and delegates all I/O to the blocking layer.

Signals:
    progress(phase: str, msg: str)  — forwarded from Orchestrator.on_progress
    done(CaptureSession)            — emitted after write_session() succeeds
    failed(str, dict)               — error description + structured info
                                      ({phase, device, command}) when the
                                      raise site is an OrchestratorError; the
                                      dict is empty for unstructured errors
                                      (write failures, unexpected exceptions).
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from core.ade9000_client import Ade9000Client
from core.distribution_client import DistributionClient
from core.orchestrator import (
    CaptureSession,
    Orchestrator,
    OrchestratorConfig,
    OrchestratorError,
)
from core.session_writer import SessionPaths, write_session


class OrchestratorWorker(QThread):
    """Execute Orchestrator.run() + write_session() in a background thread."""

    progress = Signal(str, str)       # (phase, message)
    done     = Signal(object)         # CaptureSession  (object avoids registration)
    failed   = Signal(str, dict)      # (message, {phase, device, command})

    def __init__(
        self,
        config: OrchestratorConfig,
        ade:    Ade9000Client,
        dist:   DistributionClient,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._ade    = ade
        self._dist   = dist

    def run(self) -> None:
        try:
            orc = Orchestrator(
                self._config, self._ade, self._dist,
                on_progress=self._on_progress,
            )
            session: CaptureSession = orc.run()
            self._on_progress("WRITE", f"writing session to {self._config.output_dir}")
            write_session(session)
            self.done.emit(session)
        except OrchestratorError as exc:
            self.failed.emit(str(exc), {
                "phase":   exc.phase,
                "device":  exc.device,
                "command": exc.command,
            })
        except Exception as exc:
            self.failed.emit(str(exc), {})

    def _on_progress(self, phase: str, msg: str) -> None:
        self.progress.emit(phase, msg)
