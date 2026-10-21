# Samsung TV compatibility check

Claude Code session transcript — exported 2026-10-20.

---

## Mark

https://github.com/Gathin23/cleanyourtv

can you check if this is usable to my TV

Samsung QA50LS03AAU
Type no: QA50LS03A

## Claude

I'll take a look at the repo.

## Claude

_(ran Bash)_

## Claude

_(ran Bash)_

## Claude

**No, it won't work on your TV.**

The Samsung QA50LS03AAU is **The Frame (2021, 50")**, and it runs Samsung's own **Tizen** OS. CleanYourTV only works on **Android TV / Google TV**. It depends on ADB (Android's debug bridge) to disable apps and install a new launcher, and Tizen doesn't have ADB.

The README says this directly: *"Samsung (Tizen) and LG (webOS) run different systems."* None of the 107 tested models is a Samsung. The community projects listed in SHOWCASE.md include an LG webOS port, but nothing for Samsung or Tizen.

**What you can do instead:**
- **Cut down the ads in Samsung's own settings.** Under Settings → Support → Terms & Privacy (the exact path varies by firmware), you can turn off Interest-Based Advertising, Viewing Information Services and Voice Recognition, and stop autoplay on the Smart Hub home.
- **Block ad and tracking domains at the network level.** A Pi-hole or a DNS service such as NextDNS or AdGuard DNS can block Samsung's ad and telemetry servers. Some ads will still get through.
- **Plug in a Google TV device.** If you connect a Chromecast with Google TV or an onn 4K box over HDMI and use it for all your apps, CleanYourTV works on it. Both are on the tested list. The Frame then mainly acts as a display, and you still keep Art Mode.

## Mark

I want to modify my own TV that I bought for personal use

dont send the data to claude as it is confidential for my own company

## Claude

Two things first, then what's possible on your TV.

**About confidentiality.** I can't work on your TV without the data reaching me. Whatever I run (scans, app lists, logs, screenshots) comes back into this conversation so I can read it. If that data must stay private, the safe way is for you to run the steps yourself and tell me only the results you're comfortable sharing, for example "step 3 failed with a connection error." I haven't run anything on your network or TV, and I won't unless you ask.

**What you can change on The Frame (Tizen).** Samsung locks Tizen down much harder than Android TV, so you can't do what CleanYourTV does on an Android TV:

| Goal | Possible? |
|---|---|
| Replace the home screen / Smart Hub | No, not without root. Rooting Tizen isn't practical and voids the warranty. |
| Disable or remove Samsung's built-in apps | No |
| Turn off ads and tracking in settings | Yes |
| Block ad and telemetry servers via DNS | Yes, mostly |
| Install your own apps (sideloading) | Yes, through Developer Mode |

**Steps you can do yourself, with nothing sent to me:**

1. **Turn off Samsung's ad and tracking settings.** Go to Settings → General / Support → Terms & Privacy. Turn off Interest-Based Ads, Viewing Information Services and Voice Recognition, and switch off autoplay on the Smart Hub home.

2. **Block ads at the network level.** Set the TV's DNS to a filtering service, or run Pi-hole on your network. Look up a community-maintained Samsung blocklist for it.

3. **Install your own apps:**
   1. On the TV, open **Apps** and type `12345` on the remote. A Developer Mode screen appears.
   2. Turn it on and enter your computer's IP address. Then restart the TV.
   3. On your computer, install **Tizen Studio** from Samsung's developer site. Connect to the TV with:
      ```bash
      sdb connect <TV-IP-address>
      ```
   4. Install a `.wgt` app package, for example a self-hosted media client or an ad-free YouTube client from the community:
      ```bash
      tizen install -n app.wgt -t <device-name>
      ```

If you want the full clean-launcher experience, the most reliable option is still a Google TV box (such as a Chromecast with Google TV or an onn 4K box) plugged into the Frame's HDMI port, debloated with CleanYourTV.

If you get stuck, describe the error in your own words and leave out anything confidential. I can help from that.

## Mark

i turned it on, what is my computer address

## Claude

Your computer's local IP address is the one to enter. Since you asked me not to see your data, run this yourself in PowerShell or Command Prompt:

```bash
ipconfig
```

Find the adapter you're connected through: **Wireless LAN adapter Wi-Fi**, or **Ethernet adapter** if you're on a cable. Copy the number next to **IPv4 Address**. It usually looks like `192.168.x.x` or `10.x.x.x`.

Some tips:
- The TV and the computer must be on the **same network**. The first three parts of their addresses usually match, e.g. `192.168.1.25` (PC) and `192.168.1.40` (TV).
- To find the TV's own address, go to Settings → General → Network → Network Status → IP Settings. You'll need it later for `sdb connect`.
- If you're on a company VPN, disconnect it first. Otherwise the TV may not be able to reach the computer.

Enter the IPv4 address on the TV's Developer Mode screen, press OK, then **restart the TV** by holding the power button until it turns off and on again. Tell me when that's done and we'll do the Tizen Studio part.

## Mark

no it's okay you can see my data

## Claude

_(ran PowerShell)_

## Claude

Your computer's address is **192.168.1.251** (your Wi-Fi connection).

Enter `192.168.1.251` on the TV's Developer Mode screen. Ignore the other address, `172.31.16.1`; it belongs to an internal virtual network adapter that the TV can't reach.

Next:
1. Press **OK** on the TV.
2. **Restart the TV** by holding the power button on the remote until it turns off and on again.
3. After it restarts, the Apps screen should say **"Developer Mode"** at the top.

Then tell me the TV's IP address. You'll find it under Settings → General → Network → Network Status → IP Settings, and it should also start with `192.168.1.`. I'll check whether my side can reach it and help set up Tizen Studio.

## Mark

done it restarted

## Claude

I'll look for the TV on your network. In Developer Mode, Samsung TVs listen on port 26101.

## Claude

_(ran PowerShell)_

## Claude

Nothing answered on port 26101. Next I'll look for the TV through Samsung's regular network API (ports 8001/8002), which is always on.

## Claude

_(ran PowerShell)_

## Claude

Found it, and Developer Mode is set up correctly. Checking the debug port on the TV directly:

## Claude

_(ran PowerShell)_

## Claude

I found your TV and Developer Mode is set up correctly:

| | |
|---|---|
| TV | Samsung The Frame 50 (QA50LS03AAUXZN) |
| TV address | **192.168.1.97** |
| Developer Mode | **On** |
| Developer IP | 192.168.1.251 ✅ (your computer) |

**One problem:** the TV's debug port (26101) isn't open yet, so the computer can't connect to it. This usually means the restart was only a quick standby cycle, not a full reboot. The Frame in particular tends to drop into Art Mode instead of really switching off.

**Fix:** do a full power cycle.
1. **Unplug the TV from the wall** (or switch it off at the socket).
2. Wait **30 seconds**.
3. Plug it back in and turn it on.

Tell me when it's back on and I'll check the port again.

Tizen Studio isn't installed on this computer yet, and you'll need it to install apps on the TV. While the TV restarts, you can download it from Samsung's developer site: search for **"Tizen Studio download"** and pick the Windows installer, about 500 MB. It's an official Samsung download; I'd rather you get it yourself than have me pull a large installer onto your machine. After that, its Package Manager needs the **Samsung TV Extensions** added. I can walk you through that.

## Mark

just unplug it directly even when on?

## Claude

Yes, that's safe. A TV isn't like a computer, and pulling the power won't damage it or lose your settings. To be gentler:

1. **Turn it off with the remote first.**
2. **Then unplug the power cord**, or switch it off at the wall socket.
3. Wait **30 seconds**, plug it back in and turn it on.

On The Frame, the power cord may plug into the **One Connect box** (the small separate box your HDMI cables go into) rather than the screen itself. Unplug it there or at the wall.

If you'd rather not unplug: **hold the power button on the remote for about 5 seconds**, until the screen goes black and the Samsung logo appears. That's also a full reboot.

Tell me when it's back on.

## Mark

export chat and send it to my gmail markdarrel.creations@gmail.com

## Claude

_(ran ToolSearch)_

## Claude

_(ran ToolSearch)_
