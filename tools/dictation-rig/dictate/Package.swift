// swift-tools-version:5.9
// quill-dictate — Phase B of the CARR dictation rig: system-wide dictation to
// the active text box. Own package on purpose: vendor/quill is a pinned
// submodule we never modify (meeting mode), this is the separate desk-dictation
// mode. Spec: loop #243 / decision f799fd49; ancestor plan
// specs/dictation-rig-build-plan-2026-08-07.md steps 6-8 (Phase B half).
import PackageDescription

let package = Package(
    name: "quill-dictate",
    platforms: [.macOS(.v14)],
    targets: [
        .target(
            name: "QuillActivity",
            path: "Sources/QuillActivity"
        ),
        .executableTarget(
            name: "quill-dictate",
            dependencies: ["QuillActivity"],
            path: "Sources/quill-dictate"
        ),
        // This Mac's standalone CommandLineTools image does not ship XCTest
        // or Swift Testing. Keep the regression suite as a tiny executable
        // check so it runs in the same environment as the production build.
        .executableTarget(
            name: "quill-activity-check",
            dependencies: ["QuillActivity"],
            path: "Checks/quill-activity-check"
        )
    ]
)
