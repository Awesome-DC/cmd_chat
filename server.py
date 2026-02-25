import socket
import threading
import time

rooms = {}
rooms_lock = threading.Lock()

BANNER = """
╔══════════════════════════════════════╗
║         TermChat Server              ║
║         Running on port 9999         ║
╚══════════════════════════════════════╝
"""

def handle_client(conn, addr):
    try:
        # Send welcome banner
        conn.send(b"\r\n")
        conn.send(b"  ================================\r\n")
        conn.send(b"        Welcome to TermChat       \r\n")
        conn.send(b"  ================================\r\n\r\n")
        conn.send(b"  Enter room code: ")

        # Get room code
        code = ""
        while True:
            char = conn.recv(1).decode(errors="ignore")
            if char in ("\r", "\n"):
                break
            if char == "\x7f" or char == "\x08":  # backspace
                if code:
                    code = code[:-1]
                    conn.send(b"\x08 \x08")
            else:
                code += char
                conn.send(char.encode())

        code = code.strip().upper()
        if not code:
            conn.send(b"\r\n  Invalid code. Disconnecting.\r\n")
            conn.close()
            return

        conn.send(b"\r\n")

        partner = None

        with rooms_lock:
            if code in rooms and rooms[code] is not None:
                partner = rooms[code]
                rooms[code] = None  # mark room as full
            else:
                rooms[code] = conn

        if partner is None:
            # Waiting for partner
            conn.send(b"  Room created! Waiting for partner...\r\n")
            timeout = 300  # 5 minutes
            start = time.time()
            while True:
                with rooms_lock:
                    if rooms.get(code) is None:
                        # Partner joined, find them
                        break
                if time.time() - start > timeout:
                    conn.send(b"\r\n  Timed out waiting. Disconnecting.\r\n")
                    with rooms_lock:
                        rooms.pop(code, None)
                    conn.close()
                    return
                time.sleep(0.2)

            # Find partner - they stored themselves, then we cleared it
            # We need a different approach to pass partner conn
            # Use a dict with list
            pass

        chat(conn, partner, code)

    except Exception as e:
        print(f"[ERROR] {addr}: {e}")
    finally:
        try:
            conn.close()
        except:
            pass


# Better room management
waiting_rooms = {}  # code -> conn (waiting person)
waiting_lock = threading.Lock()

def handle_client_v2(conn, addr):
    try:
        conn.send(b"\r\n")
        conn.send(b"  ================================\r\n")
        conn.send(b"       Welcome to TermChat        \r\n")
        conn.send(b"  ================================\r\n\r\n")
        conn.send(b"  Enter room code: ")

        code = ""
        while True:
            try:
                char = conn.recv(1).decode(errors="ignore")
            except:
                return
            if char in ("\r", "\n"):
                break
            if char in ("\x7f", "\x08"):
                if code:
                    code = code[:-1]
                    conn.send(b"\x08 \x08")
            elif char.isprintable():
                code += char
                conn.send(char.encode())

        code = code.strip().upper()
        conn.send(b"\r\n")

        if not code:
            conn.send(b"  Invalid code. Disconnecting.\r\n")
            conn.close()
            return

        partner_conn = None
        i_am_waiting = False

        with waiting_lock:
            if code in waiting_rooms:
                partner_conn = waiting_rooms.pop(code)
            else:
                waiting_rooms[code] = conn
                i_am_waiting = True

        if i_am_waiting:
            conn.send(b"  Room created! Waiting for partner to join...\r\n")
            timeout = 300
            start = time.time()
            while True:
                with waiting_lock:
                    if code not in waiting_rooms:
                        # Partner removed us, they have our conn
                        break
                if time.time() - start > timeout:
                    conn.send(b"\r\n  Timed out. No one joined. Disconnecting.\r\n")
                    with waiting_lock:
                        waiting_rooms.pop(code, None)
                    conn.close()
                    return
                time.sleep(0.3)

            # Partner will call chat() with us, just wait here
            # Actually we need event-based approach
            # Let's use an event dict
            pass

        else:
            # We are the joiner, partner is waiting
            conn.send(b"  Partner found! Connecting...\r\n")
            try:
                partner_conn.send(b"\r\n  [Partner joined! You can start chatting]\r\n")
                partner_conn.send(b"  [Type your message and press Enter. Type /quit to leave]\r\n\r\n")
                conn.send(b"  [Connected! You can start chatting]\r\n")
                conn.send(b"  [Type your message and press Enter. Type /quit to leave]\r\n\r\n")
            except:
                conn.send(b"  Partner disconnected. Try again.\r\n")
                conn.close()
                return

            # Start chat threads
            stop_event = threading.Event()
            t1 = threading.Thread(target=relay, args=(conn, partner_conn, "You", stop_event))
            t2 = threading.Thread(target=relay, args=(partner_conn, conn, "Partner", stop_event))
            t1.daemon = True
            t2.daemon = True
            t1.start()
            t2.start()
            stop_event.wait()

    except Exception as e:
        print(f"[ERROR] {addr}: {e}")
    finally:
        try:
            conn.close()
        except:
            pass


def relay(sender, receiver, label, stop_event):
    """Read line from sender, forward to receiver with label"""
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
                    # Send to receiver
                    try:
                        receiver.send(f"\r\n  Them: {msg}\r\n  You: ".encode())
                    except:
                        stop_event.set()
                        return
                    # Echo prompt back to sender
                    try:
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
                    sender.send(chunk)  # echo
                except:
                    break
    except:
        pass
    finally:
        stop_event.set()


# Use event-based waiting
connected_events = {}  # code -> {"event": Event, "partner": conn}
events_lock = threading.Lock()

def handle_client_final(conn, addr):
    print(f"[+] Connection from {addr}")
    try:
        conn.send(b"\r\n")
        conn.send(b"  ================================\r\n")
        conn.send(b"       Welcome to TermChat        \r\n")
        conn.send(b"  ================================\r\n\r\n")
        conn.send(b"  Enter room code: ")

        code = read_line(conn)
        if code is None:
            return
        code = code.strip().upper()
        conn.send(b"\r\n")

        if not code:
            conn.send(b"  Invalid code. Disconnecting.\r\n")
            return

        with events_lock:
            if code in connected_events:
                # Someone is waiting, join them
                entry = connected_events.pop(code)
                entry["partner"] = conn
                entry["event"].set()
                partner_conn = None
                joining = True
            else:
                event = threading.Event()
                entry = {"event": event, "partner": None}
                connected_events[code] = entry
                joining = False

        if not joining:
            conn.send(b"  Room created! Waiting for partner...\r\n")
            fired = entry["event"].wait(timeout=300)
            if not fired or entry["partner"] is None:
                conn.send(b"\r\n  Timed out. Disconnecting.\r\n")
                with events_lock:
                    connected_events.pop(code, None)
                return
            partner_conn = entry["partner"]
            # Notify both
            try:
                conn.send(b"\r\n  [Partner joined! Start chatting. Type /quit to leave]\r\n\r\n  You: ")
                partner_conn.send(b"\r\n  [Connected! Start chatting. Type /quit to leave]\r\n\r\n  You: ")
            except:
                return
        else:
            partner_conn = entry["partner"] if entry.get("partner") else None
            # Wait briefly for handle to set partner
            time.sleep(0.1)
            # Notify
            try:
                conn.send(b"  [Connected! Start chatting. Type /quit to leave]\r\n\r\n  You: ")
            except:
                return

        # Start relay
        stop_event = threading.Event()
        t1 = threading.Thread(target=relay, args=(conn, partner_conn, "You", stop_event), daemon=True)
        t2 = threading.Thread(target=relay, args=(partner_conn, conn, "Them", stop_event), daemon=True)
        t1.start()
        t2.start()
        stop_event.wait()
        print(f"[-] Chat ended for room: {code}")

    except Exception as e:
        print(f"[ERROR] {addr}: {e}")
    finally:
        try:
            conn.close()
        except:
            pass


def read_line(conn):
    """Read a line character by character, echoing back"""
    line = ""
    try:
        while True:
            conn.settimeout(60)
            char = conn.recv(1).decode(errors="ignore")
            if char in ("\r", "\n"):
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


def main():
    print(BANNER)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", 9999))
    server.listen(100)
    print("[*] Server listening on port 9999...")

    while True:
        try:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_client_final, args=(conn, addr), daemon=True)
            t.start()
        except KeyboardInterrupt:
            print("\n[*] Shutting down server.")
            break
        except Exception as e:
            print(f"[ERROR] Accept failed: {e}")

    server.close()


if __name__ == "__main__":
    main()
