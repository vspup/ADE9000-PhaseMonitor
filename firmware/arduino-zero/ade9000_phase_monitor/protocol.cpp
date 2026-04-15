#include "protocol.h"

// All output is JSON Lines: one object per line, human-readable.

void sendStatusOk(const char *event, const char *name, const char *version)
{
  Serial.print(F("{\"status\":\"ok\",\"event\":\""));
  Serial.print(event);
  if (name)
  {
    Serial.print(F("\",\"fw\":\""));
    Serial.print(name);
  }
  if (version)
  {
    Serial.print(F("\",\"ver\":\""));
    Serial.print(version);
  }
  Serial.println(F("\"}"));
}

void sendStatusError(const char *reason)
{
  Serial.print(F("{\"status\":\"error\",\"reason\":\""));
  Serial.print(reason);
  Serial.println(F("\"}"));
}

void sendVoltageJson(const VoltageSnapshot &snapshot, const EventFlags &flags)
{
  Serial.print(F("{\"ts\":"));    Serial.print(snapshot.ts);
  Serial.print(F(",\"uab\":"));   Serial.print(snapshot.Uab,   2);
  Serial.print(F(",\"ubc\":"));   Serial.print(snapshot.Ubc,   2);
  Serial.print(F(",\"uca\":"));   Serial.print(snapshot.Uca,   2);
  Serial.print(F(",\"uavg\":")); Serial.print(snapshot.Uavg,  2);
  Serial.print(F(",\"unb\":"));   Serial.print(snapshot.unb,   2);
  Serial.print(F(",\"f\":"));     Serial.print(snapshot.freq,  2);
  Serial.print(F(",\"state\":")); Serial.print((uint8_t)snapshot.state);

  // Flags array — only include set flags
  Serial.print(F(",\"flags\":["));
  bool first = true;
  auto sep = [&]() { if (!first) Serial.print(','); first = false; };

  if (flags.dip)       { sep(); Serial.print(F("\"dip\"")); }
  if (flags.unbalance) { sep(); Serial.print(F("\"unb\"")); }
  if (flags.startup)   { sep(); Serial.print(F("\"startup\"")); }
  if (flags.freq_err)  { sep(); Serial.print(F("\"freq_err\"")); }

  Serial.println(F("]}"));
}
