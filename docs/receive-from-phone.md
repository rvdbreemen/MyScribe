# Receive from phone

Send a recording from your phone to MyScribe over your own Wi-Fi - no cloud
service, no app on the phone.

## How to send a recording

1. In the library, press **Receive from phone**, then **Open for 15 minutes**.
2. Point the phone's camera at the QR code and open the link.
3. Choose the recording and press **Send**. A progress bar shows the upload.
4. The phone says the recording was received. On the computer, MyScribe says
   so too, and a few seconds later the library shows the recording, with its
   transcription started or queued.

The phone and the computer must be on the same Wi-Fi network. If a recording does
not appear in the phone's file picker, save it to Files first (on an iPhone:
Share → Save to Files).

## What the phone can and cannot do

- It can send **one** audio or video file, up to 4 GB. Anything else is
  refused with a sentence, and you can try again.
- It **cannot** see your library, your transcripts or anything else in
  MyScribe. It only gets the upload page.
- The recording is transcribed with your usual settings.

## When the door closes

The door closes by itself as soon as the recording is in, after 15 minutes,
when you press **Close the door now**, or when MyScribe stops. After that the
link on the phone no longer works; open the door again for the next
recording.

## Security

- The door is a separate, upload-only listener on port 4243, reachable only
  while it is open. MyScribe itself stays reachable from this computer only.
- The link contains a new, long secret each time you open the door. Without
  it every request is refused.
- The upload travels unencrypted over your local network. Anyone on the same
  Wi-Fi who sees the QR code or link before the door closes could send one
  file in place of your phone; nobody can read anything.
- Use it on a network you trust, such as your home Wi-Fi.

## If the phone cannot connect

- **Check the network.** Both devices must be on the same Wi-Fi. A guest
  network that keeps devices apart will not work.
- **Windows Firewall** may ask once whether Python may use the network. Allow
  it, and tick **Public** as well as **Private**: Windows often files a home
  Wi-Fi as Public. MyScribe adds no firewall rule itself.
- **Use the address with numbers.** The panel shows two links. The QR code
  carries the one with the computer's IP address; the `myscribe.local` link
  depends on name lookup on your network and may not work everywhere.
