"""Execute real queue and APS receipt methods with synthetic external boundaries.

No credentials, real messages, ObjectBox, Flutter engines, or Apple connection
are used. The code under test is copied verbatim from the selected source tree.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--dart', default='dart')
parser.add_argument('--source-root', type=Path)
parser.add_argument('--emit', type=Path)
args = parser.parse_args()
root = args.source_root or Path(__file__).resolve().parents[1]
queue = (root / 'lib/services/backend/queue/queue_impl.dart').read_text()
queue = queue[queue.index('abstract class Queue'):]
service = (root / 'lib/services/rustpush/rustpush_service.dart').read_text()
def block(start, end):
    a = service.index(start)
    return service[a:service.index(end, a)]
handlers = block('  Future handleMsg(', '  bool authing')
handlers += block('  Future<void> markAsHandledAfter(', '  void doPoll(')
reflection_start = ('    final receiveStopwatch = Stopwatch()..start();\n'
                    '    final receiveId = _diagnosticHash(myMsg.id);')
if reflection_start not in service:
    reflection_start = '    Logger.info("Reflecting ${myMsg.id}");'
reflection = block(reflection_start, '  Future<Placemark?> reverseGeocode')
# The reflection tail includes the closing brace of handleMsgInner.
action = (root / 'lib/services/backend/action_handler.dart').read_text()
a = action.index('    bool shouldNotify = shouldNotifyForNewMessageGuid(m.guid!);')
action_tail = action[a:action.index('  Future<void> handleUpdatedMessage', a)]
notify_helper = ''
if '  Future<void> _notifyNewMessageBestEffort(' in action:
    a = action.index('  Future<void> _notifyNewMessageBestEffort(')
    notify_helper = action[a:action.index('  /// Checks if a GUID', a)]

fixture = r'''
import 'dart:async';
import 'protocol.dart' as api;
class GetxService {}
class RxBool {
  bool _value;
  final controller = StreamController<bool>.broadcast();
  RxBool(this._value);
  bool get value => _value;
  set value(bool v) { _value = v; controller.add(v); }
  Stream<bool> get stream => controller.stream;
}
extension ObservableBool on bool { RxBool get obs => RxBool(this); }
class Message {
  String? guid = 'synthetic';
  int error = 0;
  static void replaceMessage(String? id, Message message) {}
  Future<void> forwardIfNessesary(Chat chat, {required bool markFailed}) async { events.add('forward'); }
}
final events = <String>[];
class Chat {
  String guid = 'synthetic-chat';
  Future<void> addMessage(Message message) async { events.add('persist'); }
}
class MessageHelper {
  static bool fail = false;
  static Future<void> handleNotification(Message message, Chat chat, {bool findExisting = true}) async {
    events.add('notify');
    if (fail) throw StateError('synthetic notification failure');
  }
}
class ActionBoundary {
  bool shouldNotifyForNewMessageGuid(String guid) => true;
NOTIFY_HELPER
  Future<void> persistAndNotify(Chat c, Message m) async {
ACTION_TAIL
}
enum QueueType { newMessage }
enum MessageError {
  BAD_REQUEST(400);
  final int code;
  const MessageError(this.code);
}
abstract class QueueItem {
  QueueType type;
  Completer<void>? completer;
  QueueItem({required this.type, this.completer});
}
class IncomingItem extends QueueItem {
  Chat chat; Message message;
  IncomingItem({required super.type, super.completer, required this.chat, required this.message});
}
class OutgoingItem extends QueueItem {
  Chat chat; Message message; Message? selected; String? reaction;
  OutgoingItem({required super.type, super.completer, required this.chat, required this.message, this.selected, this.reaction});
}
class Logger {
  static void info(Object? text, {String? tag}) {}
  static void error(Object? text, {Object? error, StackTrace? trace}) {}
  static void warn(Object? text, {String? tag, StackTrace? trace}) {}
}
class Lifecycle { bool isDead = false, isAlive = false; Timer? closeTimer; }
final ls = Lifecycle();
class Value<T> { T value; Value(this.value); }
class Settings {
  final cancelQueuedMessages = false.obs;
  final endpointUnifiedPush = Value('');
}
class SettingsService { final settings = Settings(); }
final ss = SettingsService();
class Channel { void invokeMethod(String method) {} }
final mcs = Channel();
class OutQueue { final isProcessing = false.obs; }
final outq = OutQueue();
String _diagnosticHash(String value) => 'synthetic-event';
String _durationMs(Stopwatch timer) => timer.elapsedMilliseconds.toString();
QUEUE
class TestQueue extends Queue {
  bool fail = false, prepFail = false;
  int active = 0, maxActive = 0, completed = 0;
  Completer<void>? gate;
  @override
  Future<dynamic> prepItem(QueueItem item) async {
    if (prepFail) throw StateError('synthetic preparation failure');
  }
  @override
  Future<void> handleQueueItem(QueueItem item) async {
    active++;
    if (active > maxActive) maxActive = active;
    try {
      await (gate?.future ?? Future<void>.delayed(Duration.zero));
      if (fail) throw StateError('synthetic persistence failure');
      completed++;
    } finally { active--; }
  }
}
late TestQueue inq;
class PushService {
  late Future initFuture = Future<void>.value();
  int certified = 0;
  bool reflectionFails = false;
  void markCertified(api.PushMessage push) { certified++; }
  Future<Message?> reflectMessageDyn(api.PushMessage push) async {
    if (reflectionFails) throw StateError('synthetic reflection failure');
    return Message();
  }
  Future<void> handleMsgInner(api.PushMessage myMsg) async {
    final chat = Chat();
REFLECTION
HANDLERS
}
late PushService pushService;
void check(bool ok, String message) { if (!ok) throw StateError(message); }
IncomingItem item(Completer<void> completion) => IncomingItem(
    type: QueueType.newMessage, completer: completion, chat: Chat(), message: Message());
Future<void> reset() async {
  inq = TestQueue(); pushService = PushService(); api.acks = 0;
}
Future<void> main() async {
  final errors = <String>[];
  await reset();
  inq.fail = true;
  final completion = Completer<void>();
  final outcome = completion.future.then((_) => false, onError: (_) => true);
  await inq.queue(item(completion));
  final queueFailed = await outcome;
  print('Failed persistence propagated to completion: $queueFailed');
  if (!queueFailed) errors.add('Queue silently reported success after persistence failed');
  inq.fail = false;
  final afterFailure = Completer<void>();
  await inq.queue(item(afterFailure)); await afterFailure.future;
  check(inq.completed == 1, 'Handler failure stranded later messages');

  await reset();
  pushService.reflectionFails = true;
  try { await pushService.recievedMsgPointer('pointer', '3'); } catch (_) {}
  print('Final failed reflection: acks=${api.acks} certified=${pushService.certified}');
  if (api.acks != 0 || pushService.certified != 0) errors.add('Failed push was acknowledged or certified');

  await reset();
  inq.fail = true;
  try { await pushService.recievedMsgPointer('pointer', '0'); } catch (_) {}
  print('Failed queued persistence: acks=${api.acks} certified=${pushService.certified}');
  if (api.acks != 0 || pushService.certified != 0) errors.add('Unpersisted message was acknowledged or certified');

  await reset();
  inq.gate = Completer<void>();
  final receiving = pushService.recievedMsgPointer('pointer', '0');
  await Future<void>.delayed(Duration(milliseconds: 10));
  if (api.acks != 0 || pushService.certified != 0) errors.add('Push settled before durable work finished');
  inq.gate!.complete();
  await receiving;
  check(api.acks == 1 && pushService.certified == 1 && inq.completed == 1,
      'Successful persisted push was not acknowledged exactly once');

  await reset();
  final completions = List.generate(100, (_) => Completer<void>());
  await Future.wait(completions.map((c) => inq.queue(item(c))));
  await Future.wait(completions.map((c) => c.future));
  check(inq.maxActive == 1 && inq.completed == 100, 'Burst lost or concurrently processed work');

  await reset();
  inq.prepFail = true;
  final prep = Completer<void>();
  final prepOutcome = prep.future.then((_) => false, onError: (_) => true);
  try { await inq.queue(item(prep)); } catch (_) {}
  final prepFailed = await prepOutcome.timeout(Duration(milliseconds: 50), onTimeout: () => false);
  if (!prepFailed) errors.add('Preparation failure left completion pending');
  inq.prepFail = false;
  final next = Completer<void>();
  await inq.queue(item(next)); await next.future;
  check(inq.completed == 1, 'Preparation failure prevented later work');

  events.clear(); MessageHelper.fail = true;
  try { await ActionBoundary().persistAndNotify(Chat(), Message()); } catch (_) {}
  await Future<void>.delayed(Duration.zero);
  print('Notification failure persistence order: $events');
  if (events.isEmpty || events.first != 'persist') errors.add('Notification failure prevented durable message storage');
  events.clear(); MessageHelper.fail = false;
  await ActionBoundary().persistAndNotify(Chat(), Message());
  await Future<void>.delayed(Duration.zero);
  if (events.join(',') != 'persist,forward,notify') errors.add('Notification preceded durable storage');

  if (errors.isNotEmpty) throw StateError(errors.join('; '));
  print('PASS: persistence errors, final failure, durable ack ordering, 100-item burst, preparation recovery, notification isolation');
}
'''.replace('QUEUE', queue).replace('REFLECTION', reflection).replace('HANDLERS', handlers).replace('NOTIFY_HELPER', notify_helper).replace('ACTION_TAIL', action_tail)

protocol = '''int acks = 0;
class PushMessage { String get id => 'synthetic-message'; }
Future<PushMessage?> ptrToDart({required String ptr}) async => PushMessage();
Future<void> completeMsg({required String ptr}) async { acks++; }
'''
def write(directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'incoming_test.dart').write_text(fixture)
    (directory / 'protocol.dart').write_text(protocol)
    return directory / 'incoming_test.dart'
if args.emit:
    print(write(args.emit))
else:
    with tempfile.TemporaryDirectory(prefix='openbubbles-incoming-') as directory:
        subprocess.run([args.dart, str(write(Path(directory)))], check=True)
