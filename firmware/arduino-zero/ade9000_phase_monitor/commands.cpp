#include "commands.h"
#include "protocol.h"

// Supported commands (MVP stubs — parser expanded in Stage 4):
//   PING
//   START
//   STOP
//   ARM
//   SET threshold <value>

static char cmdBuf[64];
static uint8_t cmdLen = 0;

void commandsInit()
{
  cmdLen = 0;
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

        if (strcmp(cmdBuf, "PING") == 0)
          sendStatusOk("pong");
        // Future: START, STOP, ARM, SET threshold ...

        cmdLen = 0;
      }
    }
    else if (cmdLen < (sizeof(cmdBuf) - 1))
    {
      cmdBuf[cmdLen++] = c;
    }
  }
}
