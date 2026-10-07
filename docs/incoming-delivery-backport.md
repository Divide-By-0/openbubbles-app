# Incoming delivery backport and Premium compatibility

This batch selects receive-path changes from
[upstream PR 226](https://github.com/OpenBubbles/openbubbles-app/pull/226), reviewed
at `742296e3b5740f484117de5e7ce5570baee09ca9`, primarily authored by Xare123.
It is a selective backport, not a merge of every change in that PR.

## Reproduced and fixed

The original queue catches a handler failure and then completes its caller's
completer successfully. In a faithful Dart VM reproduction of the production
class, a breakpoint at that successful completion observed a failed persistence
handler and zero completed writes. The incoming caller therefore cannot learn
that its message was not saved.

The original receive path also certifies a final failed handling attempt and
removes its native pending pointer. Finally, notification work precedes message
persistence; a notification exception can prevent saving the message at all.

Selected changes:

- Propagate preparation/handler failures through the queue completion and keep
  draining later work. Use one guarded drain loop.
- Await this message's queue completion before certification or pointer removal;
  do not acknowledge a failed handler, including its final attempt.
- Persist a new message before forwarding and notification work, isolate
  notification failures, and deduplicate in-flight handling by message GUID.
- Restore the forwarding marker when native SMS/MMS forwarding throws, so that
  a subsequent transport attempt can retry the operation.
- Retain content-free receive timing diagnostics from the upstream patch.

`tool/test_incoming_delivery.py` executes production queue, receipt methods,
reflection tail, and persistence/notification tail, stubbing external boundaries.
The pre-fix source fails for swallowed storage errors, failure acknowledgements,
premature certification, unfinished preparation, and notification-before-save.
The patched source passes, including a 100-item burst and processing after a
failure. Burst serialization is adjacent coverage; it does not establish that
the original queue produced two concurrent runners in this reproduction.

`tool/test_send_recovery.py` covers the previous confirmed-receipt recovery fix.
CI runs both harnesses, the upstream Flutter queue tests, and a real Android APK
build. Harnesses do not verify ObjectBox concurrency, Android engine lifecycle,
or live Apple delivery. The observed APS status-2 handshake failures are a
separate unresolved issue. The bounded native retry loop still eventually drops
repeatedly unhandled pointers; this patch does not provide an offline durable
inbox or indefinite transport retries.

## Selection limits

Relay Socket.IO/FCM changes and native terminal-panic loop changes from PR 226
are omitted. The reported setup uses direct Apple push. A terminal loop break
also requires separate lifecycle-recovery verification, so stopping that loop is
not an established fix for this incident. PR 227's widget/lifecycle changes and
PR 250's send/profile retries were reviewed but are not needed for the reproduced
persistence/acknowledgement failure. PR 260 is closed and addresses a different
receipt-error badge path.

## Premium and installation

Repository changes alone have no effect on the installed app or the subscription.
This batch preserves the product `monthly_hosted`, package/flavors, billing code,
hosted purchase-token settings, and original developer's hardware endpoints.
The code validates the saved purchase token at `hw.openbubbles.app/restore` and
can continue using a cached token even when a new Play purchase query fails.
That is source evidence, not a successful entitlement test on a custom build.

The CI APK is the existing `alpha` flavor, whose package is
`com.bluebubbles.messaging.alpha`. Production uses `com.openbubbles.messaging`.
A custom-signed production APK cannot update the installed Play-signed app in
place. Android requires a matching signing identity for updates, and ordinary
Play Billing is restricted for apps not signed/uploaded through the developer's
Play account. Keeping the same package alone is insufficient.

Sources: [Android signing](https://developer.android.com/studio/publish/app-signing),
[Play Billing test restrictions](https://developer.android.com/google/play/billing/test).

Maintaining payments in the existing official app does not prove that a separate
alpha build can restore its entitlement or hardware registration. An official
developer-signed build carrying these fixes offers the straightforward update
path. A private fork installation needs a separately verified entitlement and
data-migration path before replacing the working app. No purchase-token export,
app removal, reset, billing bypass, subscription cancellation, or replacement
installation is part of this batch.
