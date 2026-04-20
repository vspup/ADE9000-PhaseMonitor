#include "commands.h"
#include "../protocol/protocol.h"
#include "calibration.h"
#include "mode_manager.h"
#include "work_mode.h"

// Commands:
//   PING
//   SET MODE delta|wye            — measurement mode (ACCMODE)
//   SET WMODE monitor|capture     — operational (work) mode
//   GET WMODE
//   CAL START | CAL PHASE <A|B|C> | CAL READ | CAL APPLY <v> | CAL SAVE | CAL EXIT

static char    cmdBuf[64];
static uint8_t cmdLen    = 0;
static bool    overflow  = false;

void commandsInit()
{
  cmdLen   = 0;
  overflow = false;
}

static void dispatchCommand(char *buf)
{
  char *tok1 = strtok(buf, " ");
  if (!tok1) return;
  char *tok2 = strtok(nullptr, " ");
  char *tok3 = strtok(nullptr, " ");

  if (strcmp(tok1, "PING") == 0) {
    sendStatusOk("pong");
    return;
  }

  if (strcmp(tok1, "SET") == 0 && tok2 && strcmp(tok2, "MODE") == 0 && tok3) {
    if (strcmp(tok3, "delta") == 0) {
      modeSet(MODE_MEASURE_DELTA);
      sendStatusOk("mode_set");
    } else if (strcmp(tok3, "wye") == 0) {
      modeSet(MODE_MEASURE_WYE);
      sendStatusOk("mode_set");
    } else {
      sendStatusError("bad_mode");
    }
    return;
  }

  if (strcmp(tok1, "SET") == 0 && tok2 && strcmp(tok2, "WMODE") == 0 && tok3) {
    if (strcmp(tok3, "monitor") == 0) {
      workModeSet(WORK_MODE_MONITOR);
      sendWorkModeOk("monitor");
    } else if (strcmp(tok3, "capture") == 0) {
      workModeSet(WORK_MODE_CAPTURE);
      sendWorkModeOk("capture");
    } else {
      sendStatusError("bad_wmode");
    }
    return;
  }

  if (strcmp(tok1, "GET") == 0 && tok2 && strcmp(tok2, "WMODE") == 0) {
    sendWorkModeOk(workModeGetName(workModeGet()));
    return;
  }

  if (strcmp(tok1, "CAL") == 0 && tok2) {
    if (strcmp(tok2, "START") == 0) {
      calibrationEnter();
    } else if (strcmp(tok2, "EXIT") == 0) {
      calibrationExit();
    } else if (strcmp(tok2, "READ") == 0) {
      calibrationReadRms();
    } else if (strcmp(tok2, "SAVE") == 0) {
      calibrationSave();
    } else if (strcmp(tok2, "PHASE") == 0 && tok3) {
      CalPhase ph = CAL_PHASE_NONE;
      if      (strcmp(tok3, "A") == 0) ph = CAL_PHASE_A;
      else if (strcmp(tok3, "B") == 0) ph = CAL_PHASE_B;
      else if (strcmp(tok3, "C") == 0) ph = CAL_PHASE_C;
      else { sendStatusError("bad_phase"); return; }
      calibrationSelectPhase(ph);
    } else if (strcmp(tok2, "APPLY") == 0 && tok3) {
      calibrationApplyGain(atof(tok3));
    } else {
      sendStatusError("unknown_cal_cmd");
    }
    return;
  }

  sendStatusError("unknown_cmd");
}

void commandsProcess()
{
  while (Serial.available())
  {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r')
    {
      if (overflow) {
        sendStatusError("cmd_overflow");
      } else if (cmdLen > 0) {
        cmdBuf[cmdLen] = '\0';
        dispatchCommand(cmdBuf);
      }
      cmdLen   = 0;
      overflow = false;
    }
    else if (cmdLen < (sizeof(cmdBuf) - 1))
    {
      cmdBuf[cmdLen++] = c;
    }
    else
    {
      overflow = true;
    }
  }
}
