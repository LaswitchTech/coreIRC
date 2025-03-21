#!/usr/bin/env python3
import socket
import threading
import sys
from threading import Event
from prompt_toolkit import prompt
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.shortcuts import print_formatted_text

SERVER_HOST = '127.0.0.1'
SERVER_PORT = 6667
current_user = None
current_channel = "LOUNGE"
client_history = InMemoryHistory()

# We'll use an Event to signal that the server disconnected
server_closed_event = Event()

client_history = InMemoryHistory()

def listen(sock):
    """Receive messages from the server and print them."""
    global current_user, current_channel

    while True:
        try:
            msg = sock.recv(1024)
            if not msg:
                print("Connection closed by the server.")
                # Signal the main thread to stop
                server_closed_event.set()
                break

            decoded = msg.decode('utf-8')
            # Check for PING
            if decoded.upper().startswith("PING "):
                # Typically the format is: PING :token
                # We'll parse out the token and send back a PONG.
                token = decoded.split(":", 1)[-1].strip()
                pong_response = f"PONG :{token}\r\n"
                sock.sendall(pong_response.encode('utf-8'))
            # Retrieve the username and channel from the message
            elif decoded.startswith("Welcome, "):
                # e.g.: "Welcome, alice! You are now in channel LOUNGE.\n"
                # Parse it out
                # This is naive string processing – you can do regex or more robust parsing if desired
                try:
                    after_comma = decoded.split(", ", 1)[1]  # e.g. "alice! You are now in channel LOUNGE.\n"
                    parts = after_comma.split("!", 1)
                    user_name = parts[0].strip()  # e.g. "alice"

                    # Then parse the rest
                    channel_part = parts[1]  # e.g. " You are now in channel LOUNGE.\n"
                    # Something like:
                    channel_str = channel_part.split("channel", 1)[1]  # " LOUNGE.\n"
                    channel_str = channel_str.replace(".", "").strip()  # "LOUNGE"

                    # Now update local state
                    current_user = user_name
                    current_channel = channel_str

                    # Print the message
                    print_formatted_text(decoded, end='')
                except Exception as e:
                    print("Error parsing welcome line:", e)
            else:
                # Otherwise just print the message
                # print(decoded, end='')
                print_formatted_text(decoded, end='')

        except ConnectionAbortedError:
            # Socket was closed on our side
            break
        except Exception as e:
            print(f"[ERROR] {e}")
            # Likely the socket is closing or an unexpected error occurred
            server_closed_event.set()
            break

def parse(line: str):
    """
    Naive parser for local commands to update current_user/current_channel
    so we can show them in the prompt. This does NOT confirm success from the server.
    """
    global current_user, current_channel
    parts = line.strip().split()
    if not parts:
        return

    cmd = parts[0].upper()

    if cmd == "/LOGIN" and len(parts) >= 2:
        # e.g. "/LOGIN alice pass"
        # Set local user name
        current_user = parts[1]

        # Possibly reset channel to LOUNGE on login
        current_channel = "LOUNGE"

    elif cmd == "/JOIN" and len(parts) >= 2:
        # e.g. "/JOIN lounge"
        new_chan = parts[1]
        current_channel = new_chan

def start():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.connect((SERVER_HOST, SERVER_PORT))
        except OSError as e:
            print(f"Unable to establish a connection to {SERVER_HOST}:{SERVER_PORT}")
            return e
            # if not hasattr(e, 'errno') and e.errno == 9:
            #     log("SERVER",f"Unhandled OSError from {addr}: {e}")
        # s.connect((SERVER_HOST, SERVER_PORT))
        print(f"Connected to server at {SERVER_HOST}:{SERVER_PORT}")

        listener = threading.Thread(
            target=listen,
            args=(s,),
            daemon=True
        )
        listener.start()

        while not server_closed_event.is_set():
            try:
                # Build the prompt string using current_user/current_channel
                if current_user:
                    prompt_str = f"({current_channel}) {current_user}> "
                else:
                    prompt_str = "> "

                line = prompt(prompt_str, history=client_history)
            except (EOFError, KeyboardInterrupt):
                print("Interrupted or EOF.")
                break

            line = line.strip()
            if not line:
                continue

            # Update local user/channel if it's /LOGIN or /JOIN
            parse(line)

            # Send to server
            s.sendall(line.encode('utf-8'))

    print("Disconnected from server.")

if __name__ == "__main__":
    start()
