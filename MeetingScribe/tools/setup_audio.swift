// Meeting Scribe one-time audio setup.
//
// Creates a Multi-Output Device ("Meeting Scribe Output") that plays sound on
// the normal output AND mirrors it into the BlackHole 2ch loopback driver, so
// the app can record everything the Mac plays. Then sets it as the default
// system output. Idempotent: re-running reuses the existing device.
//
// Build & run:  swiftc -O tools/setup_audio.swift -o /tmp/setup_audio && /tmp/setup_audio

import CoreAudio
import Foundation

let AGG_UID = "MeetingScribeOutput_UID"
let AGG_NAME = "Meeting Scribe Output"
let systemObj = AudioObjectID(kAudioObjectSystemObject)

func propertyAddress(_ selector: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector,
                               mScope: kAudioObjectPropertyScopeGlobal,
                               mElement: kAudioObjectPropertyElementMain)
}

func stringProperty(_ dev: AudioDeviceID, _ selector: AudioObjectPropertySelector) -> String? {
    var addr = propertyAddress(selector)
    var cf: CFString = "" as CFString
    var size = UInt32(MemoryLayout<CFString>.size)
    let st = withUnsafeMutablePointer(to: &cf) {
        AudioObjectGetPropertyData(dev, &addr, 0, nil, &size, $0)
    }
    return st == noErr ? (cf as String) : nil
}

func allDevices() -> [AudioDeviceID] {
    var addr = propertyAddress(kAudioHardwarePropertyDevices)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(systemObj, &addr, 0, nil, &size) == noErr else { return [] }
    var devs = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(systemObj, &addr, 0, nil, &size, &devs) == noErr else { return [] }
    return devs
}

func defaultOutputDevice() -> AudioDeviceID? {
    var addr = propertyAddress(kAudioHardwarePropertyDefaultOutputDevice)
    var dev = AudioDeviceID(0)
    var size = UInt32(MemoryLayout<AudioDeviceID>.size)
    return AudioObjectGetPropertyData(systemObj, &addr, 0, nil, &size, &dev) == noErr ? dev : nil
}

func setDefaultOutput(_ dev: AudioDeviceID) -> Bool {
    var addr = propertyAddress(kAudioHardwarePropertyDefaultOutputDevice)
    var d = dev
    return AudioObjectSetPropertyData(systemObj, &addr, 0, nil,
                                      UInt32(MemoryLayout<AudioDeviceID>.size), &d) == noErr
}

func findDevice(uid: String) -> AudioDeviceID? {
    allDevices().first { stringProperty($0, kAudioDevicePropertyDeviceUID) == uid }
}

func findDevice(nameContains needle: String) -> AudioDeviceID? {
    allDevices().first {
        (stringProperty($0, kAudioDevicePropertyDeviceNameCFString) ?? "")
            .localizedCaseInsensitiveContains(needle)
    }
}

func fail(_ msg: String) -> Never {
    print("ERROR: \(msg)")
    exit(1)
}

// Reuse an existing "Meeting Scribe Output" if present.
if let existing = findDevice(uid: AGG_UID) {
    if defaultOutputDevice() == existing {
        print("OK: \(AGG_NAME) already exists and is the default output.")
        exit(0)
    }
    guard setDefaultOutput(existing) else { fail("could not set \(AGG_NAME) as default output") }
    print("OK: \(AGG_NAME) already existed — set as default output.")
    exit(0)
}

guard let blackhole = findDevice(nameContains: "BlackHole 2ch"),
      let bhUID = stringProperty(blackhole, kAudioDevicePropertyDeviceUID)
else { fail("BlackHole 2ch device not found — is the driver installed?") }

guard let current = defaultOutputDevice(),
      let mainUID = stringProperty(current, kAudioDevicePropertyDeviceUID),
      mainUID != bhUID
else { fail("could not determine the current default output device") }
let mainName = stringProperty(current, kAudioDevicePropertyDeviceNameCFString) ?? "current output"

// "stacked" = Multi-Output Device (mirror to all sub-devices, main sets the clock).
let desc: [String: Any] = [
    "name": AGG_NAME,
    "uid": AGG_UID,
    "stacked": 1,
    "master": mainUID,
    "subdevices": [
        ["uid": mainUID],
        ["uid": bhUID, "drift": 1],
    ],
]

var aggID = AudioObjectID(0)
let st = AudioHardwareCreateAggregateDevice(desc as CFDictionary, &aggID)
guard st == noErr, aggID != 0 else { fail("AudioHardwareCreateAggregateDevice failed (status \(st))") }
usleep(300_000)  // let Core Audio publish the device before selecting it

guard setDefaultOutput(aggID) else { fail("created \(AGG_NAME) but could not set it as default output") }
print("OK: created \(AGG_NAME) (\(mainName) + BlackHole 2ch) and set it as the default output.")
