import Foundation
import PDFKit

var output: [String: [[String: String]]] = [:]
for path in CommandLine.arguments.dropFirst() {
    guard let document = PDFDocument(url: URL(fileURLWithPath: path)) else { continue }
    output[path] = (0..<document.pageCount).compactMap { index in
        guard let text = document.page(at: index)?.string, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return ["location": "PDF p.\(index + 1)", "text": text]
    }
}
let data = try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
print(String(data: data, encoding: .utf8)!)
