#import <Foundation/Foundation.h>
#import <IOBluetooth/IOBluetooth.h>

@interface TivooDelegate : NSObject <IOBluetoothRFCOMMChannelDelegate> {
    @public NSMutableData *responseData;
}
@end

@implementation TivooDelegate

- (instancetype)init {
    self = [super init];
    responseData = [NSMutableData new];
    return self;
}

- (void)rfcommChannelData:(IOBluetoothRFCOMMChannel *)rfcommChannel data:(void *)dataPointer length:(NSUInteger)dataLength {
    [responseData appendBytes:dataPointer length:dataLength];
    NSMutableString *hex = [NSMutableString new];
    const unsigned char *bytes = (const unsigned char *)dataPointer;
    for (NSUInteger i = 0; i < dataLength; i++) [hex appendFormat:@"%02X ", bytes[i]];
    NSLog(@"RX: %@", hex);
}

- (void)rfcommChannelOpenComplete:(IOBluetoothRFCOMMChannel *)rfcommChannel status:(IOReturn)error {
    NSLog(@"BT_DIAG stage=open_callback result=%d (0x%08x)", (int)error, (unsigned int)error);
}

- (void)rfcommChannelClosed:(IOBluetoothRFCOMMChannel *)rfcommChannel {
    NSLog(@"BT_DIAG stage=channel_closed");
}

@end

static NSData *buildPacket(NSData *payload) {
    NSUInteger pl = [payload length];
    NSUInteger totalLen = pl + 2;
    uint8_t lLow = totalLen & 0xFF;
    uint8_t lHigh = (totalLen >> 8) & 0xFF;
    uint16_t crc = lLow + lHigh;
    const uint8_t *pb = [payload bytes];
    for (NSUInteger i = 0; i < pl; i++) crc += pb[i];

    NSMutableData *pkt = [NSMutableData dataWithCapacity:pl + 7];
    uint8_t s = 0x01; [pkt appendBytes:&s length:1];
    [pkt appendBytes:&lLow length:1];
    [pkt appendBytes:&lHigh length:1];
    [pkt appendData:payload];
    uint8_t cl = crc & 0xFF, ch = (crc >> 8) & 0xFF;
    [pkt appendBytes:&cl length:1];
    [pkt appendBytes:&ch length:1];
    s = 0x02; [pkt appendBytes:&s length:1];
    return pkt;
}

static NSData *hexToData(NSString *hex) {
    NSMutableData *d = [NSMutableData new];
    NSString *clean = [[hex stringByReplacingOccurrencesOfString:@" " withString:@""] uppercaseString];
    for (NSUInteger i = 0; i + 1 < [clean length]; i += 2) {
        unichar c1 = [clean characterAtIndex:i];
        unichar c2 = [clean characterAtIndex:i+1];
        uint8_t val = 0;
        if (c1 >= '0' && c1 <= '9') val = (c1 - '0') << 4;
        else if (c1 >= 'A' && c1 <= 'F') val = (c1 - 'A' + 10) << 4;
        if (c2 >= '0' && c2 <= '9') val |= (c2 - '0');
        else if (c2 >= 'A' && c2 <= 'F') val |= (c2 - 'A' + 10);
        [d appendBytes:&val length:1];
    }
    return d;
}

static void runLoopFor(NSTimeInterval t) {
    // Keep dispatching initialization/callback events even if a run-loop source
    // returns early. A sleep alone does not service IOBluetooth's callbacks.
    NSTimeInterval deadline = [NSProcessInfo processInfo].systemUptime + t;
    while ([NSProcessInfo processInfo].systemUptime < deadline) {
        NSTimeInterval slice = MIN(0.05, deadline - [NSProcessInfo processInfo].systemUptime);
        if (![[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode
                                     beforeDate:[NSDate dateWithTimeIntervalSinceNow:slice]]) {
            [NSThread sleepForTimeInterval:MAX(0, slice)];
        }
    }
}

static IOBluetoothRFCOMMChannel *connectRFCOMM(IOBluetoothDevice *dev,
                                              IOBluetoothHostController *controller,
                                              TivooDelegate *delegate,
                                              void (*waitForEvents)(NSTimeInterval)) {
    // isConnected describes the device link, not readiness of this process's
    // newly-created Bluetooth client. Always allow initialization events to run.
    NSLog(@"BT_DIAG stage=initialize wait=1.0s connected=%d", [dev isConnected]);
    waitForEvents(1.0);
    if (!controller) {
        NSLog(@"BT_DIAG stage=initialize failed=no_controller");
        return nil;
    }
    for (NSUInteger poll = 0; [controller powerState] != kBluetoothHCIPowerStateON && poll < 40; poll++) {
        waitForEvents(0.1);
    }

    for (NSUInteger attempt = 1; attempt <= 3; attempt++) {
        BluetoothHCIPowerState power = [controller powerState];
        NSLog(@"BT_DIAG stage=open attempt=%lu/3 power=%d connected=%d",
              (unsigned long)attempt, (int)power, [dev isConnected]);
        if (power != kBluetoothHCIPowerStateON) {
            NSLog(@"BT_DIAG stage=open failed=controller_not_powered_on");
            return nil;
        }
        IOBluetoothRFCOMMChannel *channel = nil;
        NSTimeInterval started = [NSProcessInfo processInfo].systemUptime;
        // This API opens the baseband link too, if necessary. Do not separately
        // open/reset it: the same device may currently be playing audio.
        IOReturn err = [dev openRFCOMMChannelSync:&channel withChannelID:1 delegate:delegate];
        NSLog(@"BT_DIAG stage=open_result attempt=%lu/3 result=%d (0x%08x) channel=%d elapsed=%.3fs connected=%d",
              (unsigned long)attempt, (int)err, (unsigned int)err, channel != nil,
              [NSProcessInfo processInfo].systemUptime - started, [dev isConnected]);
        if (err == kIOReturnSuccess && channel) return channel;
        if (channel) [channel closeChannel];
        if (attempt < 3) {
            NSLog(@"BT_DIAG stage=retry wait=%lus", (unsigned long)attempt);
            // Retry in this process so initialization progress is retained.
            waitForEvents((NSTimeInterval)attempt);
        }
    }
    NSLog(@"BT_DIAG stage=open failed=attempts_exhausted");
    return nil;
}

static BOOL sendAndRecv(IOBluetoothRFCOMMChannel *ch, NSData *payload, NSTimeInterval wait) {
    NSData *pkt = buildPacket(payload);
    NSMutableString *hex = [NSMutableString new];
    const uint8_t *b = [pkt bytes];
    for (NSUInteger i = 0; i < [pkt length]; i++) [hex appendFormat:@"%02X ", b[i]];
    NSLog(@"TX: %@", hex);

    IOReturn result = [ch writeSync:(void *)[pkt bytes] length:[pkt length]];
    if (result != kIOReturnSuccess) {
        NSLog(@"BT_DIAG stage=write result=%d (0x%08x)", (int)result, (unsigned int)result);
        return NO;
    }
    runLoopFor(wait);
    return YES;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 2) {
            NSLog(@"用法: tivoo_cmd [-a MAC] [-s] <hex_payload> [hex_payload2] ...");
            NSLog(@"  -a    设备 MAC 地址（必填）");
            NSLog(@"  -s    会话模式：保持连接，发送多个 payload（用 -- 分隔）");
            NSLog(@"");
            NSLog(@"  示例: tivoo_cmd 74 64           (亮度 100%%)");
            NSLog(@"        tivoo_cmd -a AA:BB:CC:DD:EE:FF 74 64");
            NSLog(@"        tivoo_cmd -s 74 64 -- 45 00 00  (先设亮度再切时钟)");
            return 1;
        }

        NSString *macAddress = nil;
        int startArg = 1;

        // Parse flags
        while (startArg < argc) {
            if (strcmp(argv[startArg], "-a") == 0 && startArg + 1 < argc) {
                macAddress = [NSString stringWithUTF8String:argv[startArg + 1]];
                startArg += 2;
            } else if (strcmp(argv[startArg], "-s") == 0) {
                startArg += 1;
            } else {
                break;
            }
        }

        // 收集所有 payload（用 -- 分隔多个命令）
        NSMutableArray<NSData *> *payloads = [NSMutableArray new];
        NSMutableString *currentHex = [NSMutableString new];

        for (int i = startArg; i < argc; i++) {
            if (strcmp(argv[i], "--") == 0) {
                if ([currentHex length] > 0) {
                    [payloads addObject:hexToData(currentHex)];
                    [currentHex setString:@""];
                }
            } else {
                if ([currentHex length] > 0) [currentHex appendString:@" "];
                [currentHex appendString:[NSString stringWithUTF8String:argv[i]]];
            }
        }
        if ([currentHex length] > 0) {
            [payloads addObject:hexToData(currentHex)];
        }

        if ([payloads count] == 0) {
            NSLog(@"无效的 hex 数据");
            return 1;
        }

        if (!macAddress) { NSLog(@"Required: -a MAC"); return 1; }

        IOBluetoothDevice *dev = [IOBluetoothDevice deviceWithAddressString:macAddress];
        if (!dev) { NSLog(@"设备未找到"); return 1; }

        TivooDelegate *delegate = [[TivooDelegate alloc] init];
        IOBluetoothRFCOMMChannel *channel = connectRFCOMM(dev,
            [IOBluetoothHostController defaultController], delegate, runLoopFor);
        if (!channel) return 1;
        runLoopFor(0.3);

        // 发送所有 payload
        for (NSUInteger i = 0; i < [payloads count]; i++) {
            NSData *payload = [payloads objectAtIndex:i];
            if ([payload length] == 0) continue;

            NSTimeInterval wait = (i == [payloads count] - 1) ? 0.8 : 0.05;
            if (!sendAndRecv(channel, payload, wait)) { [channel closeChannel]; return 2; }
        }

        [channel closeChannel];
    }
    return 0;
}
