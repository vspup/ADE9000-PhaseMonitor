#include "protocol.h"
#include "../app/mode_manager.h"

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

void sendStatusError(const char *reason, const char *got)
{
  Serial.print(F("{\"status\":\"error\",\"reason\":\""));
  Serial.print(reason);
  if (got) {
    Serial.print(F("\",\"got\":\""));
    Serial.print(got);
  }
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

void sendWorkModeOk(const char *wmode)
{
  Serial.print(F("{\"status\":\"ok\",\"event\":\"wmode\",\"wmode\":\""));
  Serial.print(wmode);
  Serial.println(F("\"}"));
}

void sendStatusSnapshot(const char *wmode, const char *mmode,
                        bool calibrating, bool streaming)
{
  Serial.print(F("{\"status\":\"ok\",\"event\":\"status\",\"wmode\":\""));
  Serial.print(wmode);
  Serial.print(F("\",\"mmode\":\""));
  Serial.print(mmode);
  Serial.print(F("\",\"cal\":"));
  Serial.print(calibrating ? F("true") : F("false"));
  Serial.print(F(",\"streaming\":"));
  Serial.print(streaming ? F("true") : F("false"));
  Serial.println(F("}"));
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

void sendCaptureStatus(const char *state, uint16_t filled,
                       uint16_t pre, uint16_t post, uint16_t total)
{
  Serial.print(F("{\"status\":\"ok\",\"event\":\"cap_status\",\"state\":\""));
  Serial.print(state);
  Serial.print(F("\",\"filled\":"));
  Serial.print(filled);
  Serial.print(F(",\"pre\":"));
  Serial.print(pre);
  Serial.print(F(",\"post\":"));
  Serial.print(post);
  Serial.print(F(",\"total\":"));
  Serial.print(total);
  Serial.println(F("}"));
}

void sendCaptureSample(int16_t i,
                       float uab, float ubc, float uca,
                       float ia,  float ib,  float ic)
{
  Serial.print(F("{\"event\":\"cap_sample\",\"i\":"));
  Serial.print(i);
  Serial.print(F(",\"uab\":")); Serial.print(uab, 2);
  Serial.print(F(",\"ubc\":")); Serial.print(ubc, 2);
  Serial.print(F(",\"uca\":")); Serial.print(uca, 2);
  Serial.print(F(",\"ia\":"));  Serial.print(ia, 3);
  Serial.print(F(",\"ib\":"));  Serial.print(ib, 3);
  Serial.print(F(",\"ic\":"));  Serial.print(ic, 3);
  Serial.println(F("}"));
}

void sendCaptureDone(uint16_t n)
{
  Serial.print(F("{\"status\":\"ok\",\"event\":\"cap_done\",\"n\":"));
  Serial.print(n);
  Serial.println(F("}"));
}
