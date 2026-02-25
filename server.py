import socket
import threading
import time

TIMEOUT_SECONDS = 600  # 10 minutes before closing an empty room

rooms = {}
rooms_lock = threading.Lock()

BANNER = """
╔══════════════════════════════════════╗
║         NormansChat Server           ║
║         Running on port 9999         ║
╚══════════════════════════════════════╝
"""


def read_line(conn):
    """
    Read a line from the client.
    Handles both char-by-char and bulk (line-at-once) input.
    Returns the line string or None on error/timeout.
    """
    buf = b""
    try:
        conn.settimeout(TIMEOUT_SECONDS + 60)
        while True:
            chunk = conn.recv(1024)
            if not chunk:
                return None
            buf += chunk
            # Check if we have a newline yet
            if b"\n" in buf or b"\r" in buf:
                # Extract first line
                for sep in (b"\r\n", b"\n", b"\r"):
                    if sep in buf:
                        line, _ = buf.split(sep, 1)
                        return line.decode(errors="ignore").strip()
    except:
        return None


def relay(sender, receiver, sender_name, stop_event):
    """Read messages from sender and forward to receiver."""
    buf = b""
    try:
        while not stop_event.is_set():
            try:
                sender.settimeout(1.0)
                chunk = sender.recv(1024)
            except socket.timeout:
                continue
            except:
                break

            if not chunk:
                break

            buf += chunk

            while b"\n" in buf or b"\r\n" in buf:
                for sep in (b"\r\n", b"\n"):
                    if sep in buf:
                        line, buf = buf.split(sep, 1)
                        msg = line.decode(errors="ignore").strip()
                        if not msg:
                            continue

                        if msg.lower() == "/quit":
                            try:
                                receiver.send(b"\r\n  [Partner has left the chat. Goodbye!]\r\n")
                            except:
                                pass
                            stop_event.set()
                            return

                        try:
                            receiver.send(f"\r\n  {sender_name}: {msg}\r\n  You: ".encode())
                            sender.send(b"  You: ")
                        except:
                            stop_event.set()
                            return
                        break
    except:
        pass
    finally:
        stop_event.set()


def send(conn, msg):
    """Helper to safely send a message."""
    try:
        conn.send(msg if isinstance(msg, bytes) else msg.encode())
        return True
    except:
        return False


def handle_client(conn, addr):
    print(f"[+] Connection from {addr}")
    try:
        # Welcome banner
        send(conn, "\r\n")
        send(conn, "  ============================================\r\n")
        send(conn, "           Welcome to NormansChat            \r\n")
        send(conn, "  ============================================\r\n\r\n")

        # Ask for display name
        send(conn, "  Enter your display name: ")
        name = read_line(conn)
        if not name:
            send(conn, "\r\n  Invalid name. Disconnecting.\r\n")
            return
        name = name.strip()
        send(conn, f"\r\n  Hello, {name}!\r\n\r\n")

        # Ask for room ID
        send(conn, "  Enter a Room ID to create or join a chat room.\r\n")
        send(conn, "  (Both people must enter the same Room ID to connect)\r\n\r\n")
        send(conn, "  Room ID: ")
        room_id = read_line(conn)
        if not room_id:
            send(conn, "\r\n  Invalid Room ID. Disconnecting.\r\n")
            return
        room_id = room_id.strip().upper()
        send(conn, f"\r\n")

        joining = False
        partner_conn = None
        partner_name = None
        entry = None

        with rooms_lock:
            if room_id in rooms:
                # Join existing room
                entry = rooms.pop(room_id)
                partner_conn = entry["conn"]
                partner_name = entry["name"]
                joining = True
            else:
                # Create new room
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
            mins = TIMEOUT_SECONDS // 60
            send(conn, f"  Room [{room_id}] created!\r\n")
            send(conn, f"  Waiting for your partner to join...\r\n")
            send(conn, f"  (Room closes automatically in {mins} minutes if no one joins)\r\n")

            fired = entry["event"].wait(timeout=TIMEOUT_SECONDS)

            if not fired or entry.get("partner_conn") is None:
                with rooms_lock:
                    rooms.pop(room_id, None)
                send(conn, "\r\n  No one joined. Room closed. Goodbye!\r\n")
                return

            partner_conn = entry["partner_conn"]
            partner_name = entry["partner_name"]

            send(conn, f"\r\n  ----------------------------------------\r\n")
            send(conn, f"  {partner_name} joined the room!\r\n")
            send(conn, f"  You are now chatting with {partner_name}.\r\n")
            send(conn, f"  Type /quit to leave.\r\n")
            send(conn, f"  ----------------------------------------\r\n\r\n")
            send(conn, "  You: ")

        else:
            # Signal the room creator
            entry["partner_conn"] = conn
            entry["partner_name"] = name
            entry["event"].set()

            send(conn, f"  ----------------------------------------\r\n")
            send(conn, f"  Connected! Chatting with {partner_name}.\r\n")
            send(conn, f"  Type /quit to leave.\r\n")
            send(conn, f"  ----------------------------------------\r\n\r\n")
            send(conn, "  You: ")

            time.sleep(0.3)

        # Start chat relay
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
                print(f"[~] Room [{code}] expired and closed.")


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
