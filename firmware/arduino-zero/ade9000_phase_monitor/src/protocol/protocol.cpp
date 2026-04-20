#include "protocol.h"
#include "src/app/mode_manager.h"

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

void sendVoltageJson(const VoltageSnapshot &snap, const EventFlags &flags)
{
  Serial.print(F("{\"ts\":"));     Serial.print(snap.ts);
  Serial.print(F(",\"mode\":\"")); Serial.print(modeGetName(snap.mode));
  Serial.print(F("\""));

  switch (snap.mode)
  {
    case MODE_MEASURE_DELTA:
      Serial.print(F(",\"uab\":")); Serial.print(snap.Uab,  2);
      Serial.print(F(",\"ubc\":")); Serial.print(snap.Ubc,  2);
      Serial.print(F(",\"uca\":")); Serial.print(snap.Uca,  2);
      Serial.print(F(",\"uavg\":")); Serial.print(snap.Uavg, 2);
      Serial.print(F(",\"unb\":")); Serial.print(snap.unb,  2);
      Serial.print(F(",\"ia\":"));   Serial.print(snap.Ia,   3);
      Serial.print(F(",\"ib\":"));   Serial.print(snap.Ib,   3);
      Serial.print(F(",\"ic\":"));   Serial.print(snap.Ic,   3);
      Serial.print(F(",\"iavg\":")); Serial.print(snap.Iavg, 3);
      Serial.print(F(",\"iunb\":")); Serial.print(snap.Iunb, 2);
      break;

    case MODE_MEASURE_WYE:
      Serial.print(F(",\"va\":"));   Serial.print(snap.Va,   2);
      Serial.print(F(",\"vb\":"));   Serial.print(snap.Vb,   2);
      Serial.print(F(",\"vc\":"));   Serial.print(snap.Vc,   2);
      Serial.print(F(",\"vavg\":")); Serial.print(snap.Vavg, 2);
      Serial.print(F(",\"unb\":")); Serial.print(snap.unb,  2);
      Serial.print(F(",\"ia\":"));   Serial.print(snap.Ia,   3);
      Serial.print(F(",\"ib\":"));   Serial.print(snap.Ib,   3);
      Serial.print(F(",\"ic\":"));   Serial.print(snap.Ic,   3);
      Serial.print(F(",\"iavg\":")); Serial.print(snap.Iavg, 3);
      Serial.print(F(",\"iunb\":")); Serial.print(snap.Iunb, 2);
      break;

    case MODE_CALIBRATION_LN:
      Serial.print(F(",\"va\":"));   Serial.print(snap.Va, 2);
      Serial.print(F(",\"vb\":"));   Serial.print(snap.Vb, 2);
      Serial.print(F(",\"vc\":"));   Serial.print(snap.Vc, 2);
      break;
  }

  Serial.print(F(",\"f\":"));     Serial.print(snap.freq,  2);
  Serial.print(F(",\"state\":")); Serial.print((uint8_t)snap.state);

  Serial.print(F(",\"flags\":["));
  bool first = true;
  auto sep = [&]() { if (!first) Serial.print(','); first = false; };
  if (flags.dip)       { sep(); Serial.print(F("\"dip\"")); }
  if (flags.unbalance) { sep(); Serial.print(F("\"unb\"")); }
  if (flags.startup)   { sep(); Serial.print(F("\"startup\"")); }
  if (flags.freq_err)  { sep(); Serial.print(F("\"freq_err\"")); }
  Serial.println(F("]}"));
}

void sendCalibrationPhase(const char *phase)
{
  Serial.print(F("{\"status\":\"ok\",\"event\":\"cal_phase\",\"phase\":\""));
  Serial.print(phase);
  Serial.println(F("\"}"));
}

void sendCalibrationRms(const char *phase, float vrms)
{
  Serial.print(F("{\"status\":\"ok\",\"event\":\"cal_rms\",\"phase\":\""));
  Serial.print(phase);
  Serial.print(F("\",\"vrms\":"));
  Serial.print(vrms, 3);
  Serial.println(F("}"));
}

void sendCalibrationApplied(const char *phase, float gain, int32_t regVal)
{
  Serial.print(F("{\"status\":\"ok\",\"event\":\"cal_applied\",\"phase\":\""));
  Serial.print(phase);
  Serial.print(F("\",\"gain\":"));
  Serial.print(gain, 6);
  Serial.print(F(",\"reg\":"));
  Serial.print(regVal);
  Serial.println(F("}"));
}
