import Foundation

func runMemoryTests() -> Bool {
    // Native archiver only creates synthetic fixtures; untrusted decoding is pure Python.
    let expected = "Synthetic café 👋\nSecond line"
    let archive = NSArchiver.archivedData(withRootObject: NSAttributedString(string: expected))
    let hex = archive.map { String(format: "%02x", $0) }.joined()
    let invalidRoot = NSArchiver.archivedData(withRootObject: "Not an attributed string" as NSString)
    let decoded = MemoryWorker.decode([hex, "010203", invalidRoot.map { String(format: "%02x", $0) }.joined(), String(hex.dropLast(6))])
    guard decoded.count == 4, decoded[0] == expected, decoded[1] == nil, decoded[2] == nil, decoded[3] == nil else { return false }
    guard let keyed = try? NSKeyedArchiver.archivedData(withRootObject: NSAttributedString(string: expected), requiringSecureCoding: true) else { return false }
    let keyedText = MemoryWorker.decode([keyed.map { String(format: "%02x", $0) }.joined()])
    return keyedText.count == 1 && keyedText[0] == expected
}
