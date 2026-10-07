# Pending sends and service recovery

Investigated against upstream `rustpush` commit
`eed1b6332efbb17adbf5ebfa2263ad770169f75e` and RustPush submodule
`a7fab473e7a33325a760635285db2860de8e1cb0`.

## Confirmed code defect

`sendMsg` persists `sendingServiceId` while native send completion is pending.
`SendConfirm` clears it, but recipient delivery/read receipts previously did not.
On initialization, `RustPushService.onInit` finds pending markers belonging to
another service handle and unconditionally calls `markFailed`. That call changes
the message GUID to `error-protocol: Crashed while still sending-<random suffix>`.
The suffix is a local temporary identifier, not an Apple error code.

The unchanged production recovery block was executed with synthetic persistence
boundaries. Delivered and read messages belonging to an older service were both
marked failed. A Dart VM debugger breakpoint at the actual `markFailed` call
observed `item.isDelivered == true`, `item.dateRead == null`, and the current
service ID differing from the old one. Recovery had already cleared the marker
but still proceeded to fail the message.

The patch clears pending markers when an authenticated recipient receipt is
handled and preserves delivered/read messages during recovery. Sends without
receipts still follow the existing interrupted-send behavior. This does not
retry messages or change their IDs when their delivery has been confirmed.

Run `python3 tool/test_send_recovery.py`. The harness executes the production
startup block verbatim, stubbing only database and notification boundaries.
It covers delivered, read, interrupted, active, settled, and repeated recovery,
plus the production receipt branch with recipient, own-device, unauthenticated,
and missing-row boundaries.
Passing `--source <pre-fix rustpush_service.dart>` fails on confirmed delivery.
The dedicated workflow runs this regression independently of native credentials.

## Device evidence and limits

The installed Android 1.15.0 Play build showed this exact recovery error on an
outgoing message whose recipient subsequently replied. The user observed the
error before the diagnostic restart. Its private ObjectBox database could not
be inspected because the installed package is not debuggable. A recipient reply
does not establish that a delivery receipt was persisted. Thus the originating
code path is identified, but the row's receipt/marker state and the event that
changed the service handle remain unverified. Installed build/source equivalence
is also unverified.

Separate pre-restart logs showed an Apple APS handshake rejected with status 2
every 30 seconds. The pinned RustPush implementation retries connection failures
with a 30-second maximum delay and retains the activation keypair to avoid
changing the push token and invalidating subscriptions. A later diagnostic
restart connected with status 0 using the saved setup. This establishes a failed
push connection during that interval, not why Apple rejected it or what triggered
an earlier notification batch.

Upstream [delivery-integrity PR](https://github.com/OpenBubbles/openbubbles-app/pull/226)
addresses concurrent queue runners and acknowledgements after handler failure.
Those are additional candidates for burst-related receive problems. Socket.IO
changes in that PR apply to relay transport and must not be assumed to repair
direct Apple APS handshake failures.

Repeated `SetLoggerError` panics were also observed. Native startup and Flutter
startup both initialize the process-global Rust logger. Its missing one-time
guard is a distinct initialization defect; these observations do not establish
that it caused the delayed messages.

This regression does not test Android service lifecycle, ObjectBox concurrency,
native send completion, live Apple transport, or an installed replacement APK.
The patch is not a claim that multi-day receive delays are fixed.
