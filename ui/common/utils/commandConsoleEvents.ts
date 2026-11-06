/**
 * @file commandConsoleEvents.ts
 * @description The shared name and shape of the events the Command Console
 * receives through the hand-off store (`displayHandoffStore.ts`): what an AI
 * agent ran in the terminal, a plot it made, and an editor change.
 */

/** The hand-off kind that carries Command Console events. */
export const COMMAND_CONSOLE_EVENT_KIND = 'uiEvent';

/** One Command Console event as sent by the backend over the event socket. */
export interface CommandConsoleEvent {
  /** The action name, such as `terminal:agent_exec`. */
  action: string;
  /** The data that goes with the action. */
  payload: any;
}
