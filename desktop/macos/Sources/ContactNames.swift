import Contacts
import Foundation

// In-memory address-to-name labels only. Never persist or export the address book.
struct ContactNames {
    private var matches: [String: [String: String]] = [:]
    static func key(_ raw: String) -> String? {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if value.contains("@") { return "email:" + value }
        // Do not guess extensions, short codes, arbitrary IDs or international suffixes.
        guard !value.isEmpty, value.allSatisfy({ "0123456789+()- .".contains($0) }) else { return nil }
        var digits = value.filter { $0 >= "0" && $0 <= "9" }
        guard digits.count >= 7, digits.count <= 15 else { return nil }
        if digits.count == 10 && !value.hasPrefix("+") { digits = "1" + digits }
        return "phone:" + digits
    }
    mutating func add(id: String, name: String, addresses: [String]) {
        guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        for address in addresses {
            if let key = Self.key(address) { matches[key, default: [:]][id] = name }
        }
    }
    func name(for address: String) -> String? {
        guard let key = Self.key(address), let values = matches[key], values.count == 1 else { return nil }
        return values.values.first
    }
    static func load() throws -> ContactNames {
        let keys: [CNKeyDescriptor] = [CNContactFormatter.descriptorForRequiredKeys(for: .fullName), CNContactPhoneNumbersKey as CNKeyDescriptor, CNContactEmailAddressesKey as CNKeyDescriptor, CNContactIdentifierKey as CNKeyDescriptor, CNContactOrganizationNameKey as CNKeyDescriptor]
        var result = ContactNames()
        try CNContactStore().enumerateContacts(with: CNContactFetchRequest(keysToFetch: keys)) { contact, _ in
            result.add(id: contact.identifier, name: CNContactFormatter.string(from: contact, style: .fullName) ?? contact.organizationName,
                       addresses: contact.phoneNumbers.map { $0.value.stringValue } + contact.emailAddresses.map { String($0.value) })
        }
        return result
    }
}
