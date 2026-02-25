import socket
import threading
import time

TIMEOUT_SECONDS = 600  # 10 minutes before closing an empty room

# rooms[code] = {"event": Event, "partner": conn, "created_at": time, "name": str}
rooms = {}
rooms_lock = threading.Lock()

BANNER = """
╔══════════════════════════════════════╗
║         NormansChat Server           ║
║         Running on port 9999         ║
╚══════════════════════════════════════╝
"""


def read_line(conn, prompt=None):
    """Read a line from the client char by char, echoing back. Returns None on error."""
    if prompt:
        try:
            conn.send(prompt.encode())
        except:
            return None
    line = ""
    try:
        while True:
            conn.settimeout(TIMEOUT_SECONDS + 60)
            char = conn.recv(1).decode(errors="ignore")
            if char in ("\r", "\n"):
                conn.send(b"\r\n")
                return line
            elif char in ("\x7f", "\x08"):
                if line:
                    line = line[:-1]
                    conn.send(b"\x08 \x08")
            elif char.isprintable():
                line += char
                conn.send(char.encode())
    except:
        return None


def relay(sender, receiver, sender_name, stop_event):
    """Read messages from sender and forward to receiver."""
    buffer = b""
    try:
        while not stop_event.is_set():
            try:
                sender.settimeout(1.0)
                chunk = sender.recv(1)
            except socket.timeout:
                continue
            except:
                break

            if not chunk:
                break

            char = chunk.decode(errors="ignore")

            if char in ("\r", "\n"):
                if buffer:
                    msg = buffer.decode(errors="ignore").strip()
                    buffer = b""

                    if msg.lower() == "/quit":
                        try:
                            receiver.send(b"\r\n  [Partner has left the chat. Goodbye!]\r\n")
                        except:
                            pass
                        stop_event.set()
                        return

                    try:
                        receiver.send(f"\r\n  {sender_name}: {msg}\r\n  You: ".encode())
                        sender.send(b"\r  You: ")
                    except:
                        stop_event.set()
                        return
            elif char in ("\x7f", "\x08"):
                if buffer:
                    buffer = buffer[:-1]
                    try:
                        sender.send(b"\x08 \x08")
                    except:
                        break
            else:
                buffer += chunk
                try:
                    sender.send(chunk)
                except:
                    break
    except:
        pass
    finally:
        stop_event.set()


def handle_client(conn, addr):
    print(f"[+] Connection from {addr}")
    try:
        # Welcome banner
        conn.send(b"\r\n")
        conn.send(b"  ============================================\r\n")
        conn.send(b"           Welcome to NormansChat            \r\n")
        conn.send(b"  ============================================\r\n\r\n")

        # Ask for display name
        name = read_line(conn, prompt="  Enter your display name: ")
        if not name or not name.strip():
            conn.send(b"  Invalid name. Disconnecting.\r\n")
            return
        name = name.strip()

        conn.send(b"\r\n")

        # Ask for room ID
        conn.send(b"  Enter a unique Room ID to create or join a room.\r\n")
        conn.send(b"  (Share this ID with the person you want to chat with)\r\n\r\n")
        room_id = read_line(conn, prompt="  Room ID: ")
        if not room_id or not room_id.strip():
            conn.send(b"  Invalid room ID. Disconnecting.\r\n")
            return
        room_id = room_id.strip().upper()

        conn.send(b"\r\n")

        joining = False
        partner_conn = None
        partner_name = None
        entry = None

        with rooms_lock:
            if room_id in rooms:
                # Room exists — join it
                entry = rooms.pop(room_id)
                partner_conn = entry["conn"]
                partner_name = entry["name"]
                joining = True
            else:
                # Create new room and wait
                event = threading.Event()
                entry = {
                    "conn": conn,
                    "name": name,
                    "event": event,
                    "partner_conn": None,
                    "partner_name": None,
                    "created_at": time.time()
                }
                rooms[room_id] = entry

        if not joining:
            # Wait for a partner to join
            mins = TIMEOUT_SECONDS // 60
            conn.send(f"  Room [{room_id}] created!\r\n".encode())
            conn.send(f"  Waiting for someone to join... (closes in {mins} mins if empty)\r\n".encode())

            fired = entry["event"].wait(timeout=TIMEOUT_SECONDS)

            if not fired or entry.get("partner_conn") is None:
                with rooms_lock:
                    rooms.pop(room_id, None)
                conn.send(b"\r\n  No one joined. Room closed. Goodbye!\r\n")
                return

            partner_conn = entry["partner_conn"]
            partner_name = entry["partner_name"]

            try:
                conn.send(f"\r\n  [{partner_name} joined the room!]\r\n".encode())
                conn.send(b"  [Type /quit anytime to leave]\r\n\r\n")
                conn.send(b"  You: ")
            except:
                return

        else:
            # Joiner — signal the creator
            entry["partner_conn"] = conn
            entry["partner_name"] = name
            entry["event"].set()

            try:
                conn.send(f"  Connected! You joined room [{room_id}] with {partner_name}.\r\n".encode())
                conn.send(b"  [Type /quit anytime to leave]\r\n\r\n")
                conn.send(b"  You: ")
            except:
                return

            time.sleep(0.3)  # let creator receive their notification first

        # Start chat
        stop_event = threading.Event()
        t1 = threading.Thread(target=relay, args=(conn, partner_conn, name, stop_event), daemon=True)
        t2 = threading.Thread(target=relay, args=(partner_conn, conn, partner_name, stop_event), daemon=True)
        t1.start()
        t2.start()
        stop_event.wait()

        print(f"[-] Chat ended: [{room_id}] between {name} and {partner_name}")

    except Exception as e:
        print(f"[ERROR] {addr}: {e}")
    finally:
        try:
            conn.close()
        except:
            pass


def cleanup_expired_rooms():
    """Background thread: close rooms that have been waiting too long."""
    while True:
        time.sleep(30)
        now = time.time()
        with rooms_lock:
            expired = [
                code for code, entry in rooms.items()
                if now - entry.get("created_at", now) > TIMEOUT_SECONDS
            ]
            for code in expired:
                entry = rooms.pop(code)
                try:
                    entry["conn"].send(b"\r\n  [Room expired. No one joined. Goodbye!]\r\n")
                    entry["conn"].close()
                except:
                    pass
                print(f"[~] Room [{code}] expired.")


def main():
    print(BANNER)

    threading.Thread(target=cleanup_expired_rooms, daemon=True).start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", 9999))
    server.listen(100)
    print("[*] Server listening on port 9999...")
    print(f"[*] Rooms expire after {TIMEOUT_SECONDS // 60} minutes if no one joins.\n")

    while True:
        try:
            conn, addr = server.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            print("\n[*] Shutting down.")
            break
        except Exception as e:
            print(f"[ERROR] Accept: {e}")

    server.close()


if __name__ == "__main__":
    main()
