/**
 * @fileoverview Emergency park command used by the tray menu.
 *
 * The tray menu sends a "park the mount now" request to the window. This
 * module runs that request through the backend's `telescope:park` RPC method
 * and tells the user if the mount could not be parked.
 */

import { emitToast } from '../../utils/emitToast';
import { parkTelescope } from './motionService';

/**
 * Parks the telescope mount and reports a failure to the user.
 *
 * The park command is safety-related, so a failure is shown as an error
 * toast instead of being ignored.
 * @return True if the backend reported that the park command succeeded.
 */
export async function emergencyParkMount(): Promise<boolean> {
    const parked = await parkTelescope();
    if (!parked) {
        emitToast(
            'Emergency park failed. Check the mount and park it manually.',
            'error',
            'EmergencyPark'
        );
    }
    return parked;
}
