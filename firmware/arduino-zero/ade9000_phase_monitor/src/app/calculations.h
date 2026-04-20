#ifndef CALCULATIONS_H
#define CALCULATIONS_H

float calcAverage3(float a, float b, float c);
float calcUnbalancePct(float uab, float ubc, float uca, float uavg);
bool  isSignalPresent(float uab, float ubc, float uca, float minVoltage);

#endif
