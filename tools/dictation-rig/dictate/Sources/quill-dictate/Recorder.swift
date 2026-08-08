// Recorder.swift — mic capture for one utterance.
//
// AVAudioEngine tap on the input node, converted live to 16 kHz mono Int16
// (whisper-cli's preferred diet), accumulated in memory, written as a WAV on
// stop. The engine starts on trigger-down and stops on release: the mic (and
// macOS's orange mic dot) is live ONLY while Joe is actually holding the key.
// Nothing is ever recorded outside a held gesture — that is the structural
// privacy property of this mode, the counterpart of meeting mode's structural
// consent announcement.

import AVFoundation
import Foundation

final class Recorder {
    private var engine: AVAudioEngine?
    private var converter: AVAudioConverter?
    private var samples = Data()
    private var peak: Float = 0
    private let targetRate: Double = 16000
    private let queue = DispatchQueue(label: "quill-dictate.recorder")

    private(set) var isRecording = false

    /// Peak level (0..1) seen so far in this capture — the silence gate reads it.
    var peakLevel: Double { Double(peak) }
    /// Seconds of audio captured so far.
    var capturedSeconds: Double { Double(samples.count) / (2.0 * targetRate) }

    func start() throws {
        guard !isRecording else { return }
        samples.removeAll(keepingCapacity: true)
        peak = 0

        let engine = AVAudioEngine()
        let input = engine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0 else {
            throw NSError(domain: "quill-dictate", code: 10,
                          userInfo: [NSLocalizedDescriptionKey: "input device has no format (mic permission?)"])
        }
        guard let targetFormat = AVAudioFormat(commonFormat: .pcmFormatInt16,
                                               sampleRate: targetRate,
                                               channels: 1,
                                               interleaved: true),
              let converter = AVAudioConverter(from: inputFormat, to: targetFormat) else {
            throw NSError(domain: "quill-dictate", code: 11,
                          userInfo: [NSLocalizedDescriptionKey: "cannot build 16kHz converter"])
        }

        input.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) { [weak self] buffer, _ in
            self?.queue.async { self?.consume(buffer: buffer, converter: converter,
                                              targetFormat: targetFormat, inputRate: inputFormat.sampleRate) }
        }

        engine.prepare()
        try engine.start()
        self.engine = engine
        self.converter = converter
        isRecording = true
    }

    /// Stop and return the captured audio as a WAV file, or nil when the
    /// capture was empty. The caller applies the silence gate via peakLevel.
    func stop(writeTo workDir: String) -> URL? {
        guard isRecording, let engine else { return nil }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        self.engine = nil
        self.converter = nil
        isRecording = false

        // Drain the conversion queue so late buffers land before we snapshot.
        var wav: Data?
        queue.sync {
            guard !samples.isEmpty else { return }
            wav = Recorder.wavData(fromInt16Mono: samples, sampleRate: Int(targetRate))
        }
        guard let wavData = wav else { return nil }

        let dir = URL(fileURLWithPath: workDir, isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let stamp = Int(Date().timeIntervalSince1970 * 1000)
        let url = dir.appendingPathComponent("utterance-\(stamp).wav")
        do {
            try wavData.write(to: url)
            return url
        } catch {
            Log.shared.line("ERROR recorder: wav write failed: \(error)")
            return nil
        }
    }

    /// Abort without producing a file (gesture turned out to be a shortcut).
    func abort() {
        guard isRecording, let engine else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        self.engine = nil
        self.converter = nil
        isRecording = false
        queue.sync { samples.removeAll(keepingCapacity: false) }
    }

    private func consume(buffer: AVAudioPCMBuffer, converter: AVAudioConverter,
                         targetFormat: AVAudioFormat, inputRate: Double) {
        let ratio = targetRate / inputRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 32)
        guard let out = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else { return }

        var fed = false
        let inputBlock: AVAudioConverterInputBlock = { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return buffer
        }
        var error: NSError?
        let result = converter.convert(to: out, error: &error, withInputFrom: inputBlock)
        guard result != .error, error == nil, let ch = out.int16ChannelData else {
            if let error { Log.shared.line("ERROR recorder: convert failed: \(error)") }
            return
        }
        let count = Int(out.frameLength)
        guard count > 0 else { return }
        ch[0].withMemoryRebound(to: UInt8.self, capacity: count * 2) { bytes in
            samples.append(bytes, count: count * 2)
        }
        let mono = UnsafeBufferPointer(start: ch[0], count: count)
        for sample in mono {
            let level = abs(Float(sample)) / 32768.0
            if level > peak { peak = level }
        }
    }

    static func wavData(fromInt16Mono pcm: Data, sampleRate: Int) -> Data {
        var data = Data()
        let byteRate = sampleRate * 2
        func append(_ value: UInt32) { withUnsafeBytes(of: value.littleEndian) { data.append(contentsOf: $0) } }
        func append16(_ value: UInt16) { withUnsafeBytes(of: value.littleEndian) { data.append(contentsOf: $0) } }
        data.append(contentsOf: Array("RIFF".utf8))
        append(UInt32(36 + pcm.count))
        data.append(contentsOf: Array("WAVE".utf8))
        data.append(contentsOf: Array("fmt ".utf8))
        append(16)
        append16(1)                    // PCM
        append16(1)                    // mono
        append(UInt32(sampleRate))
        append(UInt32(byteRate))
        append16(2)                    // block align
        append16(16)                   // bits per sample
        data.append(contentsOf: Array("data".utf8))
        append(UInt32(pcm.count))
        data.append(pcm)
        return data
    }
}
