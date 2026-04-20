#ifndef STATE_MACHINE_H
#define STATE_MACHINE_H

#include "../../types.h"

void        stateMachineInit();
void        stateMachineUpdate(const VoltageSnapshot &snapshot, const EventFlags &flags);
SystemState stateMachineGetState();

#endif
