/**
 * One vocabulary for call status across the app.
 *
 * `empty_call` is the state a dial result alone cannot express: the callee
 * picked up, so the leg is "answered" as far as FreeSWITCH is concerned, but no
 * real conversation happened — silence, noise only, a voicemail greeting, or an
 * instant hang-up. It is computed at teardown (calls.answer_state) rather than
 * from the originate result.
 *
 * Two states are page-specific on purpose, because they describe different
 * things: `not_called` is a CONTACT that was never dialled (only the phone book
 * knows that), and `failed` is a CALL that never connected (only the activity
 * feed knows that). Shipping either on the wrong page would be a filter that
 * always returns nothing.
 */
export type CallStatusValue =
  | 'all' | 'not_called' | 'answered' | 'empty_call'
  | 'no_answer' | 'declined' | 'busy' | 'failed'

export const CALL_STATUS_LABEL: Record<CallStatusValue, string> = {
  all: 'All',
  not_called: 'Not called',
  answered: 'Answered',
  empty_call: 'Empty call',
  no_answer: 'No answer',
  declined: 'Declined',
  busy: 'Busy',
  failed: 'Failed',
}

const opt = (value: CallStatusValue) => ({ value, label: CALL_STATUS_LABEL[value] })

/** Phone-book filter (Outbound): contacts, so `not_called` applies. */
export const CONTACT_STATUS_FILTER = [
  opt('all'), opt('not_called'), opt('answered'), opt('empty_call'),
  opt('no_answer'), opt('declined'), opt('busy'),
] as const

/** Call-feed filter (Activity): real calls, so `failed` applies. */
export const CALL_STATUS_FILTER = [
  opt('all'), opt('answered'), opt('empty_call'), opt('no_answer'),
  opt('declined'), opt('busy'), opt('failed'),
] as const
