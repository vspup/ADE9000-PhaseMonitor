#ifndef EVENTS_H
#define EVENTS_H

#include "../../types.h"

void       eventsSetNominalFreq(float hz);
EventFlags detectEvents(const VoltageSnapshot &snapshot);

#endif
