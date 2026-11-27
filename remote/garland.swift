import Foundation
import CoreBluetooth

// One connection per command. The controller accepts writes without acknowledgement.
final class Garland: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    let frame: Data
    let service = CBUUID(string: "FFF0")
    let writer = CBUUID(string: "FFF3")
    var central: CBCentralManager!
    var device: CBPeripheral?
    var channel: CBCharacteristic?
    var sent = false

    init(_ frame: Data) {
        self.frame = frame
        super.init()
        central = CBCentralManager(delegate: self, queue: .main)
    }

    func centralManagerDidUpdateState(_ manager: CBCentralManager) {
        switch manager.state {
        case .poweredOn: manager.scanForPeripherals(withServices: nil)
        case .poweredOff: exit(2)
        case .unauthorized: exit(3)
        case .unsupported: exit(4)
        default: break
        }
    }

    func centralManager(_ manager: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        let name = advertisementData[CBAdvertisementDataLocalNameKey] as? String ?? peripheral.name
        guard name == "MELK-OA00009", device == nil else { return }
        device = peripheral
        peripheral.delegate = self
        manager.stopScan()
        manager.connect(peripheral)
    }

    func centralManager(_ manager: CBCentralManager, didConnect peripheral: CBPeripheral) {
        peripheral.discoverServices([service])
    }

    func centralManager(_ manager: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        exit(4)
    }

    func centralManager(_ manager: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        exit(sent ? 0 : 4)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard error == nil, let found = peripheral.services?.first(where: { $0.uuid == service }) else { exit(4) }
        peripheral.discoverCharacteristics([writer], for: found)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard error == nil, let found = service.characteristics?.first(where: {
            $0.uuid == writer && $0.properties.contains(.writeWithoutResponse)
        }) else { exit(4) }
        channel = found
        writeIfReady(peripheral)
    }

    func peripheralIsReady(toSendWriteWithoutResponse peripheral: CBPeripheral) {
        writeIfReady(peripheral)
    }

    func writeIfReady(_ peripheral: CBPeripheral) {
        guard !sent, peripheral.canSendWriteWithoutResponse, let channel = channel else { return }
        sent = true
        peripheral.writeValue(frame, for: channel, type: .withoutResponse)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
            self.central.cancelPeripheralConnection(peripheral)
            exit(0)
        }
    }
}

let args = CommandLine.arguments.dropFirst()
let bytes = args.compactMap { UInt8($0, radix: 16) }
guard args.count == 9, bytes.count == 9, bytes.first == 0x7e, bytes.last == 0xef else { exit(1) }
let garland = Garland(Data(bytes))
DispatchQueue.main.asyncAfter(deadline: .now() + 12) {
    if !garland.sent { exit(garland.device == nil ? 5 : 4) }
}
RunLoop.main.run()
