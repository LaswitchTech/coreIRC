#!/usr/bin/env python3
import socket
import threading
import time
import mysql.connector
import bcrypt
import pyfiglet
import uuid
import sys
import datetime
from auth import Auth
from prompt_toolkit import prompt
from prompt_toolkit.history import InMemoryHistory

BRAND="coreIRC"

HOST = '127.0.0.1'
PORT = 6667

DB_HOST="127.0.0.1"
DB_USERNAME="demo"
DB_PASSWORD="demo"
DB_DATABASE="demo"

FILE_VERSION="VERSION"
FILE_LOG="server.log"

PING_INTERVAL = 30      # Seconds between PINGs
PING_TIMEOUT  = 30      # Seconds to wait for a PONG before dropping a client

STOP_TIMEOUT = 5        # Seconds to wait before we stop the server

# Retrieve the Vesion
with open(FILE_VERSION, "r", encoding="utf-8") as f:
    version = f.read()

# Generate ASCII Artwork
ASCII_art = pyfiglet.figlet_format(BRAND)

# MOTD (unchanged)
MOTD = f"""
{ASCII_art}
{version}

Welcome to this IRC server!
Please be courteous and follow the rules.
--------------------------------------------

"""

auth_map = {}       # Map conn -> Auth instance
clients = {}        # Map conn -> username (None if not logged in)
ping_tracker = {}   # Map conn -> last PING sent time (or None if no pending PING)
threads = []        # Thread list
channels = {}       # e.g. { "LOUNGE": set([conn1, conn2, ...]), "mychan": set([...]) }
userChannels = {}   # Map conn -> channelName (so we know where each conn currently is)

console_history = InMemoryHistory()  # optional: track command history

# We'll keep a global reference to our server socket so we can close it on /STOP
server_socket = None

# A global flag to indicate our main loop in start should continue running
RUNNING = True

def client(conn, addr):
    """
    Each client connection is handled here.
    We'll require the client to authenticate before chatting.
    """
    log("SERVER",f"New connection from {addr}")
    clients[conn] = None  # Not logged in yet
    ping_tracker[conn] = None  # No ping in flight initially

    try:
        while True:
            try:
                msg = conn.recv(1024)
            except OSError as e:
                if not hasattr(e, 'errno') and e.errno == 9:
                    log("SERVER",f"Unhandled OSError from {addr}: {e}")
                break
            if not msg:
                break

            msg_str = msg.decode('utf-8').strip()

            # 1) Check if it is a PONG message
            #    e.g. PONG :some_token
            if msg_str.upper().startswith("PONG "):
                # A typical IRC PONG has the format "PONG :token".
                # We'll just treat anything after "PONG" as the token.
                # Mark this client as having responded.
                ping_tracker[conn] = None
                continue

            # 2) Handle login or normal chat/commands
            if clients[conn] is None:
                # The user has not authenticated yet, so we expect a /LOGIN or /TOKEN command
                if msg_str.startswith("/TOKEN ") or msg_str.startswith("/LOGIN "):

                    auth_instance = Auth(DB_HOST, DB_USERNAME, DB_PASSWORD, DB_DATABASE)  # or pass DB credentials if needed
                    authenticated = False # default to False
                    if msg_str.startswith("/TOKEN "):
                        parts = msg_str.split(" ", 1)
                        if len(parts) < 2:
                            conn.sendall(b"Usage: /TOKEN <token>\n")
                            continue

                        token = parts[1]

                        authenticated = auth_instance.login_token(token)
                    elif msg_str.startswith("/LOGIN "):
                        parts = msg_str.split(" ", 2)
                        if len(parts) < 3:
                            conn.sendall(b"Usage: /LOGIN <username> <password>\n")
                            continue

                        username = parts[1]
                        password = parts[2]

                        authenticated = auth_instance.login_basic(username, password)

                    # Check if the user is authenticated
                    if authenticated:

                        # Now check if the user can log in (has IRC>ALL)
                        if not auth_instance.has_permission("IRC>ALL", 1):
                            conn.sendall(b"You do not have permission to connect to IRC.\n")
                            # Optionally log it
                            log(auth_instance.username, "User lacks IRC>ALL permission")
                            conn.close()
                            break  # or return, to exit client thread

                        # Update Maps
                        auth_map[conn] = auth_instance
                        clients[conn] = auth_instance.username
                        channels["LOUNGE"].add(conn)
                        userChannels[conn] = "LOUNGE"

                        # Send the MOTD and welcome message
                        conn.sendall(MOTD.encode('utf-8'))
                        conn.sendall(f"Welcome, {clients[conn]}! You are now in channel {userChannels[conn]}.\n\n".encode('utf-8'))

                        # Announce to the channel
                        broadcast(f"SERVER> {clients[conn]} joined the ({userChannels[conn]}).\n", conn)
                    else:
                        conn.sendall(b"Invalid credentials.\n")
                else:
                    conn.sendall(b"Please login first using /LOGIN <username> <password>\n")
            else:
                # The user is authenticated.
                # Let's check if the message is a command (/KICK, /STOP) or normal chat

                # Check if we are running a command
                if msg_str.upper().startswith("/"):

                    user_auth = auth_map[conn]  # get the Auth instance
                    log(clients[conn], f"Initiated: {msg_str.upper()}")

                    # Retrieve the Command
                    command = msg_str.split(" ", 1)[0].replace("/", "").upper()

                    # Check if the user can run the command
                    if not user_auth.has_permission(f"IRC>{command}", 2):
                        conn.sendall(f"You do not have permission to run {command}.\n".encode('utf-8'))
                    else:
                        # Execute the command
                        if run(msg_str,conn):
                            break;
                        else:
                            continue;

                # Otherwise, treat it as normal chat
                else:
                    broadcast(f"({userChannels[conn]}) {clients[conn]}> {msg_str}\n", conn)

    except Exception as e:
        if not hasattr(e, 'errno') and e.errno == 9:
            log("SERVER",f"Unhandled Exception from {addr}: {e}")

    finally:
        log("SERVER",f"Connection closed from {addr}")
        username = clients[conn]
        if conn in clients:   # Make sure it's still in the dict
            del clients[conn]
        if conn in auth_map:
            auth_map[conn].logout()
            del auth_map[conn]
        if conn in ping_tracker:
            del ping_tracker[conn]
        conn.close()
        if username:
            log(username,"has left the chat.")
            broadcast(f"SERVER> {username} has left the chat.\n", None)

def broadcast(message, source_conn, globalBroadcast=False):
    """
    Send a message to all connections in the same channel as source_conn, except source_conn itself.
    """
    if globalBroadcast:
        # Send to all authenticated users
        for c, user_name in list(clients.items()):
            if c != source_conn and user_name is not None:
                try:
                    c.sendall(message.encode('utf-8'))
                except Exception as e:
                    log("SERVER", f"Failed to send global message: {e}")
        return

    # Message is not broadcast to all channels
    if source_conn not in userChannels:
        # If the user isn't in a channel for some reason, do nothing
        return

    channel_name = userChannels[source_conn]
    conns_in_channel = channels.get(channel_name, set())

    for c in conns_in_channel:
        if c != source_conn:
            try:
                c.sendall(message.encode('utf-8'))
            except Exception as e:
                log("SERVER", f"Failed to send message in channel {channel_name}: {e}")

def log(user=None, details=""):
    """
    Logs an event with a timestamp, the action taken, and which user performed it.
    This is a simple console logger; you can expand it to log to a file or DB.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_str = user if user else "Unknown"
    # Open the file in append mode ("a")
    with open(FILE_LOG, "a", encoding="utf-8") as file:
        file.write(f"[{timestamp}][{user_str}] {details}\n")

def run(msg_str: str, conn=None):

    # /QUIT
    if msg_str.upper().startswith("/QUIT"):
        if not conn:
            return False
        # The client is requesting to disconnect
        conn.sendall(b"SERVER> Goodbye! Disconnecting.\n")
        conn.close()  # client() will then exit in the finally block
        return True

    # /BROADCAST
    elif msg_str.upper().startswith("/BROADCAST"):
        if not conn:
            return False
        # For a user-based broadcast
        announcement = msg_str.split(" ", 1)[1]
        broadcast(f"(ALL) {clients[conn]}> ANNOUNCEMENT: {announcement}\n", conn, True)

    # /CREATE
    elif msg_str.upper().startswith("/CREATE"):

        # Check if the user is allowed to use the command
        parts = msg_str.split(" ", 2)
        if len(parts) < 3:
            if not conn:
                print("Usage: /CREATE <object> <name of the object>")
                return True
            else:
                conn.sendall(b"Usage: /CREATE <object> <name of the object>\n")
                return False
        object = parts[1].strip()
        name = parts[2].strip()

        if object.upper() == "CHANNEL":

            # Check if the channel already exist
            if name in channels:
                if not conn:
                    print(f"Channel '{name}' already exists.")
                    return True
                else:
                    conn.sendall(f"Channel '{name}' already exists.\n".encode('utf-8'))
                    return False
            elif name.upper() == "ALL":
                if not conn:
                    print(f"Channel '{name}' is reserved.")
                    return True
                else:
                    conn.sendall(f"Channel '{name}' is reserved.\n".encode('utf-8'))
                    return False
            else:
                channels[name] = set()
                if not conn:
                    print(f"Channel '{name}' created.")
                    return True
                else:
                    conn.sendall(f"Channel '{name}' created.\n".encode('utf-8'))
                    return False

        # Nothing to do
        conn.sendall(b"Unrecognized object type\n")
        return False

    # /JOIN
    elif msg_str.upper().startswith("/JOIN"):
        if not conn:
            return False

        # Check if the user is allowed to use the command
        parts = msg_str.split(" ", 1)
        if len(parts) < 2:
            conn.sendall(b"Usage: /JOIN <name of the channel>\n")
            return False
        name = parts[1].strip()

        # If channel doesn't exist, you might automatically create it,
        # or you might refuse. For now, let's require it to exist:
        if name not in channels:
            conn.sendall(f"Channel '{name}' does not exist.\n".encode('utf-8'))
            return False

        # Remove user from their old channel
        current = userChannels.get(conn, None)
        if current and conn in channels[current]:
            channels[current].remove(conn)

        # Add user to the new channel
        channels[name].add(conn)
        userChannels[conn] = name

        # Announce to the user & channel
        conn.sendall(f"You joined channel '{name}'.\n".encode('utf-8'))
        broadcast(f"SERVER> {clients[conn]} joined channel '{name}'.\n", conn)

        return False

    # /LIST
    elif msg_str.upper().startswith("/LIST"):

        # Check if the user is allowed to use the command
        parts = msg_str.split(" ", 1)
        if len(parts) < 2:
            if not conn:
                print("Usage: /LIST <object>")
                return True
            else:
                conn.sendall(b"Usage: /LIST <object>\n")
                return False
        object = parts[1].strip()

        if object.upper() == "USERS":

            # Gather the user list, ignoring None (unauthenticated)
            connected_users = [username for username in clients.values() if username is not None]

            if not connected_users:
                response = "No users are currently connected.\n"
            else:
                response = "Currently connected users:\n"
                for i, user_name in enumerate(connected_users, start=1):
                    response += f"{i}. {user_name}\n"

            # Send the list back to the requesting client (conn)
            if conn:
                conn.sendall(response.encode('utf-8'))
            else:
                print(response)
                return True
            return False
        elif object.upper() == "CHANNELS":
            # Gather all channel names
            if not channels:
                response = "No channels exist.\n"
            else:
                response = "Available channels:\n"
                for ch_name, user_set in channels.items():
                    response += f"{ch_name} - {len(user_set)} user(s)\n"

            if conn:
                conn.sendall(response.encode('utf-8'))
            else:
                print(response)
            return False

        # Nothing to do
        if not conn:
            print("Unrecognized object type")
            return True
        else:
            conn.sendall(b"Unrecognized object type\n")
            return False

    # /KICK <target_username_or_ip>
    elif msg_str.upper().startswith("/KICK"):

        # Check if the user is allowed to use the command
        parts = msg_str.split(" ", 1)
        if len(parts) < 2:
            if not conn:
                print("Usage: /KICK <username_or_ip>")
                return True
            else:
                conn.sendall(b"Usage: /KICK <username_or_ip>\n")
                return False
        target_str = parts[1].strip()
        kicked = kick(target_str)
        if not conn:
            if kicked:
                print(f"Kicked {target_str}")
                return True
            else:
                print(f"Could not find {target_str} to kick.")
                return True
        else:
            if kicked:
                conn.sendall(f"Kicked {target_str}\n".encode('utf-8'))
            else:
                conn.sendall(f"Could not find {target_str} to kick.\n".encode('utf-8'))

    # /STOP (stop the server)
    elif msg_str.upper() == "/STOP":

        # Stop the server
        stop()
        # Break out of client so this thread ends too
        return True

    return False

def kick(target_str):
    """
    Attempts to kick a user based on username or IP address.
    Returns True if found & kicked, False otherwise.
    """
    # Convert IP address to string for comparison if we do an addr check
    # But we currently only store 'conn -> username' in clients. We don't store an 'addr'.
    # We'll do a quick approach by scanning for:
    #   1) clients[conn] == target_str (username)
    #   2) str(conn.getpeername()) == target_str  if we want to match IP/port
    kicked_someone = False
    for conn, user in list(clients.items()):
        if user == target_str:
            # Found user by username -> Kick
            conn.close()  # triggers client to exit
            kicked_someone = True
        else:
            # Optionally match IP or "ip:port"
            ip_port = f"{conn.getpeername()[0]}:{conn.getpeername()[1]}"
            if target_str in ip_port:
                conn.close()
                kicked_someone = True
    return kicked_someone

def console():
    """
    Runs in a separate thread, using prompt_toolkit to read commands
    from the server's own console (stdin).
    Lets you type commands like /STOP directly into the terminal.
    """
    global RUNNING

    while RUNNING:
        try:
            # Display a prompt and wait for user input
            line = prompt("CONSOLE> ", history=console_history)
            line = line.strip()
        except (EOFError, KeyboardInterrupt):
            # If we get Ctrl+C or an EOF, just break out
            log("SERVER","Console loop ended.")
            break

        if not line:
            continue  # If the user just hits enter on an empty line, do nothing

        if line.upper().startswith("/"):
            # It's a command
            if not run(line):
                print(f"Unrecognized console command: {line}")
        else:
            # We treat it as a broadcast to everyone
            broadcast(f"CONSOLE> {line}\n", None, True)

    print("Exiting console loop.")

def monitor():
    """
    Periodically send PING to all clients.
    If they don't respond with PONG within PING_TIMEOUT, drop them.
    """
    while RUNNING:
        time.sleep(PING_INTERVAL)
        # Send ping to each client that is authenticated (clients[conn] != None)
        for conn, user in list(clients.items()):
            if user is not None:  # Only ping authenticated users
                if ping_tracker[conn] is None:
                    # No outstanding ping -> send a new PING
                    token = uuid.uuid4()
                    msg = f"PING :{token}\r\n"
                    try:
                        conn.sendall(msg.encode('utf-8'))
                        ping_tracker[conn] = time.time()
                    except Exception as e:
                        log("SERVER",f"Failed to send PING to {user}: {e}")
                else:
                    # There's an outstanding ping
                    elapsed = time.time() - ping_tracker[conn]
                    if elapsed > PING_TIMEOUT:
                        # The client didn't respond in time -> drop it
                        log("SERVER",f"{user} did not respond to PING. Disconnecting.")
                        conn.close()  # This triggers client() to exit.

def stop():
    """
    Stop accepting new connections and close the server socket.
    This will effectively make start() exit.
    """
    global RUNNING

    # IF the server is running, announce all users and close their connection
    if RUNNING:
        # Announce a countdown before we stop the server
        remaining = STOP_TIMEOUT
        while remaining > 0:
            if remaining > 60:
                # Round up to the nearest minute
                minutes_left = (remaining + 59) // 60  # e.g., 70 sec => 2 min
                broadcast_msg = f"SERVER> Server shutting down in {minutes_left} minute(s)...\n"
                console_msg = f"Server shutting down in {minutes_left} minute(s)..."

                # Sleep interval is 60 seconds or the remainder if less
                step = min(60, remaining)
            elif remaining > 5:
                # Between 6 and 60 seconds left: notify every 10 seconds
                broadcast_msg = f"SERVER> Server shutting down in {remaining} seconds...\n"
                console_msg = f"Server shutting down in {remaining} seconds..."

                step = min(10, remaining - 5)
            else:
                # 5 seconds or less: broadcast every second
                broadcast_msg = f"SERVER> Server shutting down in {remaining} second(s)...\n"
                console_msg = f"Server shutting down in {remaining} second(s)..."

                step = 1

            # Do the broadcasts/prints
            broadcast(broadcast_msg, None, True)
            print(console_msg)

            # Wait for 'step' seconds
            time.sleep(step)

            # Reduce the remaining time
            remaining -= step

        broadcast("SERVER> Goodbye! Server is shutting down now.\n", None, True)

        # Optionally close all existing connections
        for c in list(clients.keys()):
            try:
                c.sendall(b"SERVER> Connection closed by server shutdown.\n")
                c.close()
            except:
                pass

    # Set the RUNNING status to False
    RUNNING = False

    # Close the Socket
    if server_socket:
        try:
            server_socket.close()  # This will break out of server.accept()
        except:
            pass

def start():
    """
    Start the server, accept new connections, and handle them in separate threads.
    Also start the monitor in a background thread.
    """

    # Create our first channel
    global channels
    # Create the default "LOUNGE" channel
    channels["LOUNGE"] = set()

    # Create a socket for our server
    global server_socket
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()
    print(f"{MOTD}")
    log("SERVER",f"IRC server started on {HOST}:{PORT}")

    # Make server socket globally accessible
    server_socket = server

    # 1) Start the monitor in a separate thread
    thread_ping = threading.Thread(target=monitor)
    thread_ping.start()
    threads.append(thread_ping)

    # 2) Start the admin console loop in a separate thread
    #    so you can type commands in the terminal
    thread_console = threading.Thread(target=console)
    thread_console.start()
    threads.append(thread_console)

    while RUNNING:
        try:
            conn, addr = server.accept()
        except OSError:
            # This happens when server.close() is called, breaking accept()
            break

        thread_client = threading.Thread(target=client, args=(conn, addr))
        thread_client.start()
        threads.append(thread_client)

    print("Server is stopping...")

    # Wait for all threads to finish gracefully
    for thread in threads:
        thread.join()

    log("SERVER","All threads have stopped.")
    print("Server has stopped.")

if __name__ == "__main__":
    start()
