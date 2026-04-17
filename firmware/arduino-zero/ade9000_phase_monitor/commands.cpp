#include "commands.h"
#include "protocol.h"
#include "calibration.h"

// Supported commands:
//   PING
//   CAL START
//   CAL PHASE <A|B|C>
//   CAL READ
//   CAL APPLY <voltage>
//   CAL SAVE
//   CAL EXIT

static char    cmdBuf[64];
static uint8_t cmdLen = 0;

void commandsInit()
{
  cmdLen = 0;
}

static void dispatchCommand(char *buf)
{
  // Split into up to 3 tokens: verb [sub] [arg]
  char *tok1 = strtok(buf, " ");
  if (!tok1) return;

  char *tok2 = strtok(nullptr, " ");
  char *tok3 = strtok(nullptr, " ");

  if (strcmp(tok1, "PING") == 0) {
    sendStatusOk("pong");
    return;
  }

  if (strcmp(tok1, "CAL") == 0 && tok2) {
    if (strcmp(tok2, "START") == 0) {
      calibrationEnter();
      return;
    }
    if (strcmp(tok2, "EXIT") == 0) {
      calibrationExit();
      return;
    }
    if (strcmp(tok2, "READ") == 0) {
      calibrationReadRms();
      return;
    }
    if (strcmp(tok2, "SAVE") == 0) {
      calibrationSave();
      return;
    }
    if (strcmp(tok2, "PHASE") == 0 && tok3) {
      CalPhase ph = CAL_PHASE_NONE;
      if (strcmp(tok3, "A") == 0) ph = CAL_PHASE_A;
      else if (strcmp(tok3, "B") == 0) ph = CAL_PHASE_B;
      else if (strcmp(tok3, "C") == 0) ph = CAL_PHASE_C;
      else { sendStatusError("bad_phase"); return; }
      calibrationSelectPhase(ph);
      return;
    }
    if (strcmp(tok2, "APPLY") == 0 && tok3) {
      float vReal = atof(tok3);
      calibrationApplyGain(vReal);
      return;
    }
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
      if (cmdLen > 0)
      {
        cmdBuf[cmdLen] = '\0';
        dispatchCommand(cmdBuf);
        cmdLen = 0;
      }
    }
    else if (cmdLen < (sizeof(cmdBuf) - 1))
    {
      cmdBuf[cmdLen++] = c;
    }
  }
}
