# DeskFlow

DeskFlow is a lightweight, wireless KVM utility written in Python. It allows sharing a single mouse, keyboard, and rich clipboard (including text and images) between two computers on the same local network.

## Features
* **Wireless Mouse Roaming**: Border edge detection switches control seamlessly.
* **Keyboard Redirection**: Captures and routes input (including modifier keys) with local suppression.
* **Rich Clipboard Sync**: Synchronizes text and images with fast zlib compression and loop/freeze protection.
* **TLS Encryption**: Secure communication via SSL/TLS socket wrappers.

## Emergency Exit
If the mouse/keyboard focus becomes stuck on the client, press **`Ctrl + Alt + Shift + Escape`** on the server keyboard to immediately break the connection and restore local control.

## Getting Started

### Setup & Run
1. Download the executable from the [Latest Release](https://github.com/parm2006/DeskFlow/releases/latest) and run `DeskFlow.exe` on both computers.
2. On the **Server (Host)**: Enter a password, select the Client position, and click **Start Server**.
3. On the **Client**: Enter the Server's IP address, Port, password, and click **Connect**.
