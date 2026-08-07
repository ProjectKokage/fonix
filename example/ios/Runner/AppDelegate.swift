import Flutter
import UIKit

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  private static let referenceLaunchChannelName = "dev.fonix.reference/launch"
  private static let referenceLaunchMethod = "readSmokeActivation"
  private static let referenceSmokeKey = "FONIX_REFERENCE_SMOKE"
  private static let referenceChallengeKey = "FONIX_REFERENCE_CHALLENGE"

  private enum ReferenceLaunchActivation {
    case absent
    case active(String)
    case invalid
  }

  private var referenceLaunchChannel: FlutterMethodChannel?
  private lazy var referenceLaunchActivation = Self.readReferenceLaunchActivation()

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    let channel = FlutterMethodChannel(
      name: Self.referenceLaunchChannelName,
      binaryMessenger: engineBridge.applicationRegistrar.messenger()
    )
    channel.setMethodCallHandler { call, result in
      guard call.method == Self.referenceLaunchMethod else {
        result(FlutterMethodNotImplemented)
        return
      }
      guard call.arguments == nil else {
        result(
          FlutterError(
            code: "invalid-reference-launch-call",
            message: "The reference launch activation request is invalid.",
            details: nil
          )
        )
        return
      }
      switch self.referenceLaunchActivation {
      case .absent:
        result(nil)
      case .active(let challenge):
        result(["schemaVersion": 1, "challenge": challenge])
      case .invalid:
        result(
          FlutterError(
            code: "invalid-reference-smoke-activation",
            message: "The reference smoke activation is invalid.",
            details: nil
          )
        )
      }
    }
    referenceLaunchChannel = channel
  }

  private static func readReferenceLaunchActivation() -> ReferenceLaunchActivation {
    let environment = ProcessInfo.processInfo.environment
    let smoke = environment[referenceSmokeKey]
    let challenge = environment[referenceChallengeKey]
    if smoke == nil && challenge == nil {
      return .absent
    }
    guard
      smoke == "1",
      let challenge,
      Self.isLowercaseHexChallenge(challenge)
    else {
      return .invalid
    }
    return .active(challenge)
  }

  private static func isLowercaseHexChallenge(_ value: String) -> Bool {
    let bytes = Array(value.utf8)
    return bytes.count == 64 && bytes.allSatisfy { byte in
      (byte >= 48 && byte <= 57) || (byte >= 97 && byte <= 102)
    }
  }
}
