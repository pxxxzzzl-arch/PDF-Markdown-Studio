#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) {
            fprintf(stderr, "usage: generate_icon OUTPUT.png\n");
            return 2;
        }

        const NSInteger pixelSize = 1024;
        NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc]
            initWithBitmapDataPlanes:NULL
                          pixelsWide:pixelSize
                          pixelsHigh:pixelSize
                       bitsPerSample:8
                     samplesPerPixel:4
                            hasAlpha:YES
                            isPlanar:NO
                      colorSpaceName:NSCalibratedRGBColorSpace
                         bitmapFormat:0
                          bytesPerRow:0
                         bitsPerPixel:0];
        if (bitmap == nil) {
            fprintf(stderr, "failed to allocate icon bitmap\n");
            return 1;
        }
        bitmap.size = NSMakeSize(pixelSize, pixelSize);
        NSGraphicsContext *context =
            [NSGraphicsContext graphicsContextWithBitmapImageRep:bitmap];
        if (context == nil) {
            fprintf(stderr, "failed to create icon graphics context\n");
            return 1;
        }
        [NSGraphicsContext saveGraphicsState];
        [NSGraphicsContext setCurrentContext:context];

        [[NSColor colorWithCalibratedRed:0.95 green:0.93 blue:0.88 alpha:1] setFill];
        [[NSBezierPath bezierPathWithRoundedRect:NSMakeRect(0, 0, 1024, 1024)
                                         xRadius:220
                                         yRadius:220] fill];
        [[NSColor colorWithCalibratedRed:0.12 green:0.14 blue:0.12 alpha:1] setFill];
        [[NSBezierPath bezierPathWithRoundedRect:NSMakeRect(154, 154, 716, 716)
                                         xRadius:145
                                         yRadius:145] fill];

        NSMutableParagraphStyle *paragraph = [[NSMutableParagraphStyle alloc] init];
        paragraph.alignment = NSTextAlignmentCenter;
        NSDictionary *attributes = @{
            NSFontAttributeName : [NSFont monospacedSystemFontOfSize:246
                                                             weight:NSFontWeightMedium],
            NSForegroundColorAttributeName : NSColor.whiteColor,
            NSParagraphStyleAttributeName : paragraph,
        };
        [@"M↓" drawInRect:NSMakeRect(174, 342, 676, 300) withAttributes:attributes];
        [context flushGraphics];
        [NSGraphicsContext restoreGraphicsState];
        NSData *png = [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
        NSURL *destination =
            [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]]];
        if (![png writeToURL:destination atomically:YES]) {
            fprintf(stderr, "failed to render icon\n");
            return 1;
        }
    }
    return 0;
}
