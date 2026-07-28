#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>
#import <WebKit/WebKit.h>
#import <signal.h>

@interface PDFMDAppDelegate
    : NSObject <NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate,
                WKDownloadDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) WKWebView *webView;
@property(nonatomic, strong) NSTask *serverProcess;
@property(nonatomic, strong) NSFileHandle *serverLog;
@property(nonatomic, strong) NSURL *runtimeDirectory;
@end

@implementation PDFMDAppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    [self createWindow];
    [self startServer];
    [NSApp activateIgnoringOtherApps:YES];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    return YES;
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    [self stopServer];
}

- (void)windowWillClose:(NSNotification *)notification {
    [NSApp terminate:nil];
}

- (void)createWindow {
    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    configuration.websiteDataStore = WKWebsiteDataStore.defaultDataStore;
    configuration.defaultWebpagePreferences.allowsContentJavaScript = YES;

    self.webView = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:configuration];
    self.webView.navigationDelegate = self;
    self.webView.UIDelegate = self;
    [self.webView loadHTMLString:[self.class loadingPage] baseURL:nil];

    self.window = [[NSWindow alloc]
        initWithContentRect:NSMakeRect(0, 0, 1180, 780)
                  styleMask:(NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                             NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
                    backing:NSBackingStoreBuffered
                      defer:NO];
    self.window.title = @"PDF Markdown Studio";
    self.window.minSize = NSMakeSize(960, 680);
    self.window.delegate = self;
    self.window.contentView = self.webView;
    [self.window center];
    [self.window makeKeyAndOrderFront:nil];
}

- (void)startServer {
    NSURL *resources = NSBundle.mainBundle.resourceURL;
    NSURL *applicationSupport = [NSFileManager.defaultManager
        URLForDirectory:NSApplicationSupportDirectory
               inDomain:NSUserDomainMask
      appropriateForURL:nil
                 create:YES
                  error:nil];
    if (resources == nil || applicationSupport == nil) {
        [self showFailure:@"无法定位应用资源或用户数据目录。"];
        return;
    }

    NSURL *executable = [[resources URLByAppendingPathComponent:@"server" isDirectory:YES]
        URLByAppendingPathComponent:@"pdfmd-desktop-server"];
    if (![NSFileManager.defaultManager isExecutableFileAtPath:executable.path]) {
        [self showFailure:@"内置转换服务缺失，请重新安装应用。"];
        return;
    }

    NSURL *dataDirectory =
        [applicationSupport URLByAppendingPathComponent:@"PDF Markdown Studio" isDirectory:YES];
    NSURL *logsDirectory = [dataDirectory URLByAppendingPathComponent:@"logs" isDirectory:YES];
    NSURL *runtimeDirectory = [NSFileManager.defaultManager.temporaryDirectory
        URLByAppendingPathComponent:
            [NSString stringWithFormat:@"pdf-markdown-studio-%d",
                                       NSProcessInfo.processInfo.processIdentifier]
                     isDirectory:YES];
    NSError *error = nil;
    if (![NSFileManager.defaultManager createDirectoryAtURL:logsDirectory
                                withIntermediateDirectories:YES
                                                 attributes:nil
                                                      error:&error]) {
        [self showFailure:[NSString stringWithFormat:@"无法创建用户数据目录：%@",
                                                     error.localizedDescription]];
        return;
    }
    [NSFileManager.defaultManager removeItemAtURL:runtimeDirectory error:nil];
    if (![NSFileManager.defaultManager createDirectoryAtURL:runtimeDirectory
                                withIntermediateDirectories:YES
                                                 attributes:nil
                                                      error:&error]) {
        [self showFailure:[NSString stringWithFormat:@"无法创建运行目录：%@",
                                                     error.localizedDescription]];
        return;
    }

    NSURL *logURL = [logsDirectory URLByAppendingPathComponent:@"desktop-server.log"];
    if (![NSFileManager.defaultManager fileExistsAtPath:logURL.path]) {
        [NSFileManager.defaultManager createFileAtPath:logURL.path contents:nil attributes:nil];
    }
    NSFileHandle *logHandle = [NSFileHandle fileHandleForWritingAtPath:logURL.path];
    [logHandle seekToEndOfFile];
    NSURL *portFile = [runtimeDirectory URLByAppendingPathComponent:@"port"];

    NSTask *process = [[NSTask alloc] init];
    process.executableURL = executable;
    process.arguments = @[ @"--port-file", portFile.path ];
    NSMutableDictionary<NSString *, NSString *> *environment =
        [NSProcessInfo.processInfo.environment mutableCopy];
    environment[@"PDFMD_DATA_DIR"] = dataDirectory.path;
    environment[@"PDFMD_DESKTOP"] = @"1";
    environment[@"PYTHONUNBUFFERED"] = @"1";
    environment[@"DOCLING_INFERENCE_COMPILE_TORCH_MODELS"] = @"false";
    environment[@"TOKENIZERS_PARALLELISM"] = @"false";
    NSURL *bundledModelCache =
        [[resources URLByAppendingPathComponent:@"model-cache" isDirectory:YES]
            URLByAppendingPathComponent:@"huggingface" isDirectory:YES];
    BOOL hasBundledModels = [NSFileManager.defaultManager
        fileExistsAtPath:[[bundledModelCache URLByAppendingPathComponent:@"hub"
                                                             isDirectory:YES] path]];
    NSURL *cacheRoot = [NSFileManager.defaultManager
        URLForDirectory:NSCachesDirectory
               inDomain:NSUserDomainMask
      appropriateForURL:nil
                 create:YES
                  error:nil];
    if (hasBundledModels) {
        NSString *bundledHubCache =
            [[bundledModelCache URLByAppendingPathComponent:@"hub" isDirectory:YES] path];
        environment[@"HF_HOME"] = bundledModelCache.path;
        environment[@"HF_HUB_CACHE"] = bundledHubCache;
        environment[@"HUGGINGFACE_HUB_CACHE"] = bundledHubCache;
        environment[@"TRANSFORMERS_CACHE"] = bundledHubCache;
        environment[@"HF_HUB_OFFLINE"] = @"1";
        environment[@"TRANSFORMERS_OFFLINE"] = @"1";
        environment[@"HF_HUB_DISABLE_TELEMETRY"] = @"1";
        [environment removeObjectForKey:@"DOCLING_ARTIFACTS_PATH"];
    } else if (cacheRoot != nil) {
        NSURL *modelCache =
            [cacheRoot URLByAppendingPathComponent:@"PDF Markdown Studio" isDirectory:YES];
        environment[@"HF_HOME"] =
            [[modelCache URLByAppendingPathComponent:@"huggingface" isDirectory:YES] path];
    }
    if (cacheRoot != nil) {
        NSURL *modelCache =
            [cacheRoot URLByAppendingPathComponent:@"PDF Markdown Studio" isDirectory:YES];
        environment[@"PADDLE_HOME"] =
            [[modelCache URLByAppendingPathComponent:@"paddle" isDirectory:YES] path];
    }
    process.environment = environment;
    process.standardOutput = logHandle;
    process.standardError = logHandle;

    if (![process launchAndReturnError:&error]) {
        [self showFailure:[NSString stringWithFormat:@"启动内置服务失败：%@",
                                                     error.localizedDescription]];
        return;
    }
    self.serverProcess = process;
    self.serverLog = logHandle;
    self.runtimeDirectory = runtimeDirectory;
    [self waitForServer:process portFile:portFile];
}

- (void)waitForServer:(NSTask *)process portFile:(NSURL *)portFile {
    __weak typeof(self) weakSelf = self;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
      for (NSInteger attempt = 0; attempt < 300; attempt++) {
          if (!process.running) {
              dispatch_async(dispatch_get_main_queue(), ^{
                [weakSelf showFailure:@"内置服务意外退出，请重新打开应用。"];
              });
              return;
          }
          NSString *rawPort = [NSString stringWithContentsOfURL:portFile
                                                       encoding:NSASCIIStringEncoding
                                                          error:nil];
          NSInteger port = rawPort.integerValue;
          if (port > 0 && [self.class isHealthyOnPort:port]) {
              NSURL *url =
                  [NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%ld/",
                                                                 (long)port]];
              dispatch_async(dispatch_get_main_queue(), ^{
                [weakSelf.webView loadRequest:[NSURLRequest requestWithURL:url]];
              });
              return;
          }
          [NSThread sleepForTimeInterval:0.1];
      }
      dispatch_async(dispatch_get_main_queue(), ^{
        [weakSelf showFailure:@"应用在 30 秒内未能完成启动，请关闭后重试。"];
      });
    });
}

+ (BOOL)isHealthyOnPort:(NSInteger)port {
    NSURL *url = [NSURL
        URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%ld/api/health", (long)port]];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url];
    request.timeoutInterval = 0.5;
    dispatch_semaphore_t completed = dispatch_semaphore_create(0);
    __block BOOL healthy = NO;
    NSURLSessionDataTask *task = [NSURLSession.sharedSession
        dataTaskWithRequest:request
          completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
            if (data != nil && error == nil) {
                NSString *text = [[NSString alloc] initWithData:data
                                                       encoding:NSUTF8StringEncoding];
                healthy = [text containsString:@"\"status\":\"ok\""];
            }
            dispatch_semaphore_signal(completed);
          }];
    [task resume];
    long result = dispatch_semaphore_wait(
        completed, dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.7 * NSEC_PER_SEC)));
    if (result != 0) {
        [task cancel];
        return NO;
    }
    return healthy;
}

- (void)stopServer {
    if (self.serverProcess.running) {
        [self.serverProcess terminate];
        for (NSInteger attempt = 0; attempt < 20 && self.serverProcess.running; attempt++) {
            [NSThread sleepForTimeInterval:0.05];
        }
        if (self.serverProcess.running) {
            kill(self.serverProcess.processIdentifier, SIGKILL);
        }
    }
    [self.serverLog closeFile];
    if (self.runtimeDirectory != nil) {
        [NSFileManager.defaultManager removeItemAtURL:self.runtimeDirectory error:nil];
    }
}

- (void)showFailure:(NSString *)message {
    NSString *escaped = [message stringByReplacingOccurrencesOfString:@"&" withString:@"&amp;"];
    escaped = [escaped stringByReplacingOccurrencesOfString:@"<" withString:@"&lt;"];
    escaped = [escaped stringByReplacingOccurrencesOfString:@">" withString:@"&gt;"];
    [self.webView loadHTMLString:[self.class failurePage:escaped] baseURL:nil];
}

- (void)webView:(WKWebView *)webView
    runOpenPanelWithParameters:(WKOpenPanelParameters *)parameters
              initiatedByFrame:(WKFrameInfo *)frame
             completionHandler:(void (^)(NSArray<NSURL *> *_Nullable URLs))completionHandler {
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.title = @"选择 PDF 文件";
    panel.prompt = @"选择";
    panel.message = parameters.allowsMultipleSelection
                        ? @"可以一次选择多份 PDF 进行批量转换。"
                        : @"请选择一份 PDF 文件。";
    panel.canChooseFiles = YES;
    panel.canChooseDirectories = NO;
    panel.allowsMultipleSelection = parameters.allowsMultipleSelection;
    panel.allowedContentTypes = @[ UTTypePDF ];
    [panel beginSheetModalForWindow:self.window
                 completionHandler:^(NSModalResponse result) {
                   completionHandler(result == NSModalResponseOK ? panel.URLs : nil);
                 }];
}

- (void)webView:(WKWebView *)webView
    decidePolicyForNavigationAction:(WKNavigationAction *)navigationAction
                    decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    if (@available(macOS 11.3, *)) {
        if (navigationAction.shouldPerformDownload) {
            decisionHandler(WKNavigationActionPolicyDownload);
            return;
        }
    }
    decisionHandler(WKNavigationActionPolicyAllow);
}

- (void)webView:(WKWebView *)webView
    navigationAction:(WKNavigationAction *)navigationAction
         didBecomeDownload:(WKDownload *)download API_AVAILABLE(macos(11.3)) {
    download.delegate = self;
}

- (void)webView:(WKWebView *)webView
    navigationResponse:(WKNavigationResponse *)navigationResponse
         didBecomeDownload:(WKDownload *)download API_AVAILABLE(macos(11.3)) {
    download.delegate = self;
}

- (void)download:(WKDownload *)download
    decideDestinationUsingResponse:(NSURLResponse *)response
                 suggestedFilename:(NSString *)suggestedFilename
                 completionHandler:(void (^)(NSURL *_Nullable))completionHandler
    API_AVAILABLE(macos(11.3)) {
    dispatch_async(dispatch_get_main_queue(), ^{
      NSSavePanel *panel = [NSSavePanel savePanel];
      panel.nameFieldStringValue = suggestedFilename;
      panel.canCreateDirectories = YES;
      [panel beginSheetModalForWindow:self.window
                   completionHandler:^(NSModalResponse result) {
                     completionHandler(result == NSModalResponseOK ? panel.URL : nil);
                   }];
    });
}

- (void)downloadDidFinish:(WKDownload *)download API_AVAILABLE(macos(11.3)) {}

- (void)download:(WKDownload *)download
    didFailWithError:(NSError *)error
          resumeData:(NSData *)resumeData API_AVAILABLE(macos(11.3)) {
    dispatch_async(dispatch_get_main_queue(), ^{
      NSAlert *alert = [[NSAlert alloc] init];
      alert.messageText = @"下载失败";
      alert.informativeText = error.localizedDescription;
      alert.alertStyle = NSAlertStyleWarning;
      [alert beginSheetModalForWindow:self.window completionHandler:nil];
    });
}

+ (NSString *)loadingPage {
    return @"<!doctype html><html lang='zh-CN'><meta charset='utf-8'><style>"
           ":root{color-scheme:light;font-family:-apple-system,BlinkMacSystemFont,sans-serif}"
           "body{margin:0;min-height:100vh;display:grid;place-items:center;color:#20231f;"
           "background:#f3f0e8}main{display:grid;justify-items:center;gap:16px}"
           "i{width:42px;height:42px;border:2px solid #d8d4c8;border-top-color:#b94727;"
           "border-radius:50%;animation:spin .9s linear infinite}strong{font-size:18px}"
           "span{color:#73766e;font-size:13px}@keyframes spin{to{transform:rotate(360deg)}}"
           "</style><main><i></i><strong>PDF Markdown Studio</strong>"
           "<span>正在启动本地转换服务…</span></main></html>";
}

+ (NSString *)failurePage:(NSString *)message {
    return [NSString
        stringWithFormat:
            @"<!doctype html><html lang='zh-CN'><meta charset='utf-8'><style>"
             ":root{color-scheme:light;font-family:-apple-system,BlinkMacSystemFont,sans-serif}"
             "body{margin:0;min-height:100vh;display:grid;place-items:center;color:#20231f;"
             "background:#f3f0e8}main{width:min(480px,calc(100vw - 64px));padding:32px;"
             "border:1px solid #d8d4c8;border-radius:16px;background:#fffefb;"
             "box-shadow:0 18px 50px rgba(45,45,37,.08)}b{display:block;color:#b94727;"
             "font-size:13px;letter-spacing:.08em}h1{margin:12px 0;font-size:24px}"
             "p{color:#73766e;line-height:1.7;font-size:14px}</style>"
             "<main><b>STARTUP ERROR</b><h1>应用没有成功启动</h1><p>%@</p>"
             "<p>请关闭窗口后重新打开；若问题持续，可重新安装应用。</p></main></html>",
            message];
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *application = NSApplication.sharedApplication;
        PDFMDAppDelegate *delegate = [[PDFMDAppDelegate alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
