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
    """Read a full line from client. Returns string or None on error."""
    buf = b""
    try:
        conn.settimeout(TIMEOUT_SECONDS + 60)
        while True:
            chunk = conn.recv(1024)
            if not chunk:
                return None
            buf += chunk
            for sep in (b"\r\n", b"\n", b"\r"):
                if sep in buf:
                    line, _ = buf.split(sep, 1)
                    return line.decode(errors="ignore").strip()
    except:
        return None


def relay(sender, receiver, sender_name, stop_event):
    """
    Read messages from sender, forward to receiver.
    Format sent to receiver:  MSG:<name>:<message>
    Nothing is sent back to sender (no echo).
    """
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

            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                msg = line.replace(b"\r", b"").decode(errors="ignore").strip()
                if not msg:
                    continue

                if msg.lower() == "/quit":
                    try:
                        receiver.send(b"SYS:Partner has left the chat. Goodbye!\n")
                    except:
                        pass
                    stop_event.set()
                    return

                # Send to receiver in a clean parseable format
                # MSG:<sender_name>:<message>
                try:
                    receiver.send(f"MSG:{sender_name}:{msg}\n".encode())
                except:
                    stop_event.set()
                    return

    except:
        pass
    finally:
        stop_event.set()


def send_msg(conn, msg):
    try:
        conn.send((msg + "\n").encode())
        return True
    except:
        return False


def handle_client(conn, addr):
    print(f"[+] Connection from {addr}")
    try:
        send_msg(conn, "SYS:Welcome to NormansChat!")
        send_msg(conn, "PROMPT:name")

        name = read_line(conn)
        if not name:
            return
        name = name.strip()
        send_msg(conn, f"SYS:Hello {name}!")

        send_msg(conn, "PROMPT:room")

        room_id = read_line(conn)
        if not room_id:
            return
        room_id = room_id.strip().upper()

        joining = False
        entry   = None

        with rooms_lock:
            if room_id in rooms:
                entry   = rooms.pop(room_id)
                joining = True
            else:
                event = threading.Event()
                entry = {
                    "conn":         conn,
                    "name":         name,
                    "event":        event,
                    "partner_conn": None,
                    "partner_name": None,
                    "created_at":   time.time()
                }
                rooms[room_id] = entry

        if not joining:
            mins = TIMEOUT_SECONDS // 60
            send_msg(conn, f"SYS:Room [{room_id}] created! Waiting for partner...")
            send_msg(conn, f"SYS:Room closes in {mins} mins if nobody joins.")

            fired = entry["event"].wait(timeout=TIMEOUT_SECONDS)

            if not fired or entry.get("partner_conn") is None:
                with rooms_lock:
                    rooms.pop(room_id, None)
                send_msg(conn, "SYS:No one joined. Room closed. Goodbye!")
                return

            partner_conn = entry["partner_conn"]
            partner_name = entry["partner_name"]

            send_msg(conn, f"CONNECTED:{partner_name}")

        else:
            partner_conn = entry["conn"]
            partner_name = entry["name"]

            entry["partner_conn"] = conn
            entry["partner_name"] = name
            entry["event"].set()

            send_msg(conn, f"CONNECTED:{partner_name}")
            # Also notify the waiting person
            try:
                partner_conn.send(f"CONNECTED:{name}\n".encode())
            except:
                pass

            time.sleep(0.2)

        # Start relay threads
        stop_event = threading.Event()
        t1 = threading.Thread(target=relay, args=(conn,         partner_conn, name,         stop_event), daemon=True)
        t2 = threading.Thread(target=relay, args=(partner_conn, conn,         partner_name, stop_event), daemon=True)
        t1.start()
        t2.start()
        stop_event.wait()

        print(f"[-] Chat ended: [{room_id}] {name} <-> {partner_name}")

    except Exception as e:
        print(f"[ERROR] {addr}: {e}")
    finally:
        try:
            conn.close()
        except:
            pass


def cleanup_expired_rooms():
    while True:
        time.sleep(30)
        now = time.time()
        with rooms_lock:
            expired = [c for c, e in rooms.items() if now - e.get("created_at", now) > TIMEOUT_SECONDS]
            for code in expired:
                e = rooms.pop(code)
                try:
                    e["conn"].send(b"SYS:Room expired. No one joined. Goodbye!\n")
                    e["conn"].close()
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
    print("[*] Listening on port 9999...")
    print(f"[*] Rooms expire after {TIMEOUT_SECONDS // 60} mins if empty.\n")

    while True:
        try:
            conn, addr = server.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            print("\n[*] Shutting down.")
            break
        except Exception as e:
            print(f"[ERROR] {e}")

    server.close()


if __name__ == "__main__":
    main()
