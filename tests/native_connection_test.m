// Exercise the production connection helper with fake devices; no radio access.
#define main tivoo_cli_main
#import "../native/tivoo_cmd.m"
#undef main
#include <assert.h>

static NSMutableArray<NSNumber *> *waits;
static void fakeWait(NSTimeInterval delay) { [waits addObject:@(delay)]; }

@interface FakeController : NSObject
@property NSUInteger polls;
@property NSUInteger offPolls;
- (BluetoothHCIPowerState)powerState;
@end
@implementation FakeController
- (BluetoothHCIPowerState)powerState {
    return self.polls++ < self.offPolls ? kBluetoothHCIPowerStateOFF : kBluetoothHCIPowerStateON;
}
@end

@interface FakeChannel : NSObject
@property NSUInteger closes;
- (IOReturn)closeChannel;
@end
@implementation FakeChannel
- (IOReturn)closeChannel { self.closes++; return kIOReturnSuccess; }
@end

@interface FakeDevice : NSObject
@property BOOL connected;
@property NSUInteger calls;
@property NSUInteger failures;
@property BOOL emptySuccess;
@property BOOL partialFailure;
@property FakeChannel *channel;
- (BOOL)isConnected;
- (IOReturn)openRFCOMMChannelSync:(IOBluetoothRFCOMMChannel **)channel
                   withChannelID:(BluetoothRFCOMMChannelID)channelID delegate:(id)delegate;
@end
@implementation FakeDevice
- (BOOL)isConnected { return self.connected; }
- (IOReturn)openRFCOMMChannelSync:(IOBluetoothRFCOMMChannel **)channel
                   withChannelID:(BluetoothRFCOMMChannelID)channelID delegate:(id)delegate {
    assert(waits.count >= 1 && [waits[0] doubleValue] == 1.0);
    assert(channelID == 1);
    self.calls++;
    BOOL fail = self.calls <= self.failures;
    *channel = ((!fail || self.partialFailure) && !self.emptySuccess)
        ? (IOBluetoothRFCOMMChannel *)self.channel : nil;
    return fail ? kIOReturnError : kIOReturnSuccess;
}
@end

static IOBluetoothRFCOMMChannel *connectFake(FakeDevice *dev, FakeController *controller) {
    waits = [NSMutableArray new];
    return connectRFCOMM((IOBluetoothDevice *)dev, (IOBluetoothHostController *)controller,
                         [TivooDelegate new], fakeWait);
}

int main(void) {
    @autoreleasepool {
        for (NSNumber *connected in @[@YES, @NO]) {
            FakeDevice *dev = [FakeDevice new];
            dev.connected = connected.boolValue;
            dev.channel = [FakeChannel new];
            assert(connectFake(dev, [FakeController new]) != nil);
            assert(dev.calls == 1 && waits.count == 1);
        }
        FakeDevice *dev = [FakeDevice new];
        dev.connected = YES;
        dev.channel = [FakeChannel new];
        dev.failures = 2;
        dev.partialFailure = YES;
        assert(connectFake(dev, [FakeController new]) != nil);
        assert(dev.calls == 3 && dev.channel.closes == 2);
        assert([waits isEqualToArray:(@[@1.0, @1.0, @2.0])]);

        dev.calls = 0;
        dev.failures = 9;
        assert(connectFake(dev, [FakeController new]) == nil);
        assert(dev.calls == 3 && waits.count == 3);

        dev.calls = 0;
        dev.failures = 0;
        dev.emptySuccess = YES;
        assert(connectFake(dev, [FakeController new]) == nil);
        assert(dev.calls == 3);  // success code without a channel is not success

        dev.calls = 0;
        dev.emptySuccess = NO;
        FakeController *controller = [FakeController new];
        controller.offPolls = 100;
        assert(connectFake(dev, controller) == nil);
        assert(dev.calls == 0 && waits.count == 41);  // bounded power-on wait

        controller = [FakeController new];
        controller.offPolls = 2;
        assert(connectFake(dev, controller) != nil);
        assert(dev.calls == 1 && waits.count == 3);

        dev.calls = 0;
        assert(connectFake(dev, nil) == nil);
        assert(dev.calls == 0);
        puts("Native connection scenarios passed");
    }
    return 0;
}
