#include "calculations.h"
#include <math.h>

float calcAverage3(float a, float b, float c)
{
  return (a + b + c) / 3.0f;
}

float calcUnbalancePct(float uab, float ubc, float uca, float uavg)
{
  if (uavg < 1.0f) return 0.0f;

  float maxDev = 0.0f;
  float d;

  d = fabsf(uab - uavg); if (d > maxDev) maxDev = d;
  d = fabsf(ubc - uavg); if (d > maxDev) maxDev = d;
  d = fabsf(uca - uavg); if (d > maxDev) maxDev = d;

  return (maxDev / uavg) * 100.0f;
}

bool isSignalPresent(float uab, float ubc, float uca, float minVoltage)
{
  return (uab > minVoltage) || (ubc > minVoltage) || (uca > minVoltage);
}
