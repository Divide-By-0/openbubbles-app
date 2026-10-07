"""Run the production startup recovery block with synthetic persistence boundaries.

This deliberately executes the source block rather than copying its algorithm.
It needs only Dart, allowing receipt/recovery regressions to run independently
of the application's native Apple credentials and Flutter plugin initialization.
It does not exercise ObjectBox, Android lifecycle, or Apple's push transport.
"""

import argparse
from pathlib import Path
import subprocess
import tempfile


parser = argparse.ArgumentParser()
parser.add_argument("--dart", default="dart")
parser.add_argument("--source", type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
source = (args.source or root / "lib/services/rustpush/rustpush_service.dart").read_text()
start = source.index("    final sendingProgress = Database.messages.query(")
end = source.index("    if (ls.isUiThread)", start)
recovery = source[start:end]
start = source.index("    if (myMsg.message is api.Message_Delivered ||")
end = source.index("    var chat = await chatForMessage(myMsg);", start)
receipt = source[start:end]

fixture = r'''
import 'protocol.dart' as api;
class Message_ { static final sendingServiceId = Field(); }
class Field { Object notNull() => Object(); }
class Query {
  Query build() => this;
  List<Message> find() => Database.rows.where((m) => m.sendingServiceId != null).toList();
}
class QuerySource { Query query(Object condition) => Query(); }
class Database {
  static final messages = QuerySource();
  static final rows = <Message>[];
}
class Message {
  String? sendingServiceId;
  final bool delivered;
  bool get isDelivered => delivered || dateDelivered != null;
  DateTime? dateDelivered;
  DateTime? dateRead;
  final chat = ChatLink();
  bool wasDeliveredQuietly = false;
  static Message? row;
  static Message? findOne({String? guid}) => row;
  int saves = 0;
  Message(this.sendingServiceId, {bool isDelivered = false, this.dateRead}) : delivered = isDelivered;
  Message save({bool updateSendingServiceId = false}) {
    if (!updateSendingServiceId) throw StateError('Pending marker was not persisted');
    saves++;
    return this;
  }
}
class ChatLink { final target = Chat(); }
class Chat {
  bool get isIMessage => true;
  bool get notifsSilenced => false;
  DateTime? get dateNotifiedAnyways => null;
  void toggleHasUnread(bool value, {required bool privateMark}) {}
}
class State { final client = Object(); }
class Service { final State? state = State(); }
final pushService = Service();
DateTime parseDate(int timestamp) => DateTime.fromMillisecondsSinceEpoch(timestamp);
enum QueueType { updatedMessage }
class IncomingItem {
  IncomingItem({required Chat chat, required Message message, required QueueType type});
}
class Queue { void queue(IncomingItem item) {} }
final inq = Queue();
class Envelope {
  final Object message;
  final String sender;
  final bool verificationFailed;
  String get id => 'synthetic-message';
  int get sentTimestamp => 1;
  Envelope(this.message, {this.sender = 'recipient', this.verificationFailed = false});
}
Future<void> handleReceipt(Envelope myMsg) async {
RECEIPT
}
final failed = <Message>[];
Future<void> markFailed(Message m, String reason) async {
  if (reason != 'Crashed while still sending') throw StateError('Unexpected failure reason');
  failed.add(m);
}
Future<void> recover(String serviceId) async {
RECOVERY
}
void check(bool ok, String reason) {
  if (!ok) throw StateError(reason);
}
Future<void> main() async {
  final delivered = Message('old', isDelivered: true);
  final read = Message('old', dateRead: DateTime(2026));
  final interrupted = Message('old');
  final active = Message('current');
  final settled = Message(null, isDelivered: true);
  Database.rows.addAll([delivered, read, interrupted, active, settled]);
  await recover('current');
  check(!failed.contains(delivered), 'Confirmed delivery was overwritten as failed');
  check(!failed.contains(read), 'Confirmed read was overwritten as failed');
  check(delivered.sendingServiceId == null && delivered.saves == 1,
      'Delivered send retained an obsolete pending marker');
  check(read.sendingServiceId == null && read.saves == 1,
      'Read send retained an obsolete pending marker');
  check(failed.length == 1 && failed.single == interrupted,
      'An interrupted send without a receipt must still fail');
  check(interrupted.sendingServiceId == null && interrupted.saves == 1,
      'Interrupted marker was not cleared');
  check(active.sendingServiceId == 'current' && active.saves == 0,
      'An active send in the current service was changed');
  check(settled.saves == 0, 'A settled send was unnecessarily rewritten');
  await recover('current');
  check(failed.length == 1, 'Recovery failed the same send twice');
  for (final event in [api.Message_Delivered(), api.Message_Read()]) {
    final pending = Message('current');
    Message.row = pending;
    await handleReceipt(Envelope(event));
    check(pending.sendingServiceId == null && pending.saves == 1,
        'Authenticated recipient receipt did not settle the pending send');
    check(pending.isDelivered || pending.dateRead != null, 'Receipt was not saved');
  }
  final own = Message('current');
  Message.row = own;
  await handleReceipt(Envelope(api.Message_Delivered(), sender: 'self'));
  check(own.sendingServiceId == 'current' && own.saves == 0,
      'An own-device receipt settled recipient delivery');
  await handleReceipt(Envelope(api.Message_Delivered(), verificationFailed: true));
  check(own.sendingServiceId == 'current' && own.saves == 0,
      'An unauthenticated receipt settled delivery');
  Message.row = null;
  await handleReceipt(Envelope(api.Message_Read()));
  print('PASS: six recovery cases; delivered/read receipt settlement; own-device, unverified, missing-row receipts');
}
'''.replace("RECOVERY", recovery).replace("RECEIPT", receipt)

with tempfile.TemporaryDirectory(prefix="openbubbles-recovery-") as directory:
    script = Path(directory) / "recovery_test.dart"
    (Path(directory) / "protocol.dart").write_text(
        "class Message_Delivered {}\nclass Message_Read {}\n"
        "Future<List<String>> getHandles({required Object state}) async => ['self'];\n"
    )
    script.write_text(fixture)
    subprocess.run([args.dart, str(script)], check=True)
