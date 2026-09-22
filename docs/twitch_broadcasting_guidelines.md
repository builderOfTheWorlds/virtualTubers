
Broadcasting Guidelines

See Twitch Ingest Recommendations for ingest server recommendations.

Our guidelines are set up in a way to find the right balance between visual quality and playback quality, where both the broadcaster and the viewer can benefit from. Read the info below to help you choose the Encoding, Bitrate, Resolution, and Framerate settings that provide the right balance for the game you're playing, your internet speed, and your computer's hardware. Remember: it's always better to have a stable stream than to push for a higher video quality that might cause you to drop frames or test the limits of your internet connection.
On this page:

    Encoding
    Internet Speed (bitrate)
    Video Quality
    Audio Quality
    Other Useful Info

Encoding

Encoding Performance

Encoding can be taxing on your system. x264 will utilize a lot of your CPU, resulting in lower FPS. Alternatively, GPU encoding (e.g. NVIDIA NVENC) utilizes a dedicated encoder in the GPU, allowing you to play and stream without compromising game performance. If you want to use x264, start with veryfast preset, and experiment with them until you find your sweet spot.

Encoding Quality

x264 offers a wide range of presets that change quality significantly, and presets above Faster require CPUs with 6+ cores. NVIDIA NVENC offers consistent quality based on the generation of the encoder. The updated NVIDIA Encoder (NVENC) on Turing-based NVIDIA GeForce GPUs (RTX 20-Series and GTX 1660/Ti) will typically produce superior quality than x264 Fast and on par with x264 medium. While the older generation (Pascal, Kepler) are similar with veryfast/faster quality.
 
Internet Speed (bitrate)

Your ingest bitrate is the amount of data you send to Twitch when you stream. A higher bitrate takes up more of your available internet bandwidth. Increasing your bitrate can improve your video quality, but only up to a certain point-- our recommended bitrate settings have been tested to optimize video quality without wasting bandwidth.

CBR vs VBR

You may have noticed the option to select between VBR (variable bitrate) and CBR (constant bitrate). Twitch suggests that all Broadcasters use CBR for several reasons, all of which relate to the final quality of service (QoS) that your viewers will experience.

The main problem with VBR is that during lulls in the action: paused games, hero selection screens, etc, VBR streams produce a significantly lower bitrate. This can cause issues (stream buffering, dropped frames) with many end-user connections when the bitrate spikes back up during the action (team fights, high motion scenes).

VBR also leads to issues when sending data to the Twitch network over many ISPs. When connecting to Twitch, the route your ISP takes may not be stable enough to handle sudden increases in bandwidth, leading to "broadcast starvation."

Broadcast starvation (instability) is when video data does not arrive in a timely fashion or is completely missing. This happens when there are network problems between you and Twitch, when you attempt to stream at a bitrate that is too high for your network/ISP, or have problems with your router causing frames to be dropped before entering the Twitch network. This leads to lag for everyone (including people watching on your lower resolutions), buffering, and stream disconnections.

Variable bitrate is suboptimal for video over the internet due to how TCP (the internet protocol most streaming video uses) works, and you should use CBR whenever possible.
 
Video Quality

Resolution refers to the size of a video on a screen, and frame rate refers to how often animation frames are sent to Twitch. Full HD resolution is typically 1080p, 60 frames per second (fps). Streaming at a higher resolution like 1080p requires a higher bitrate, and a higher frame rate takes more encoding power. If you have the bandwidth and encoding power to stream at 1080p, 60 fps, great! If not, try one of the recommended settings below to optimize your video quality and stability.
 
Audio Quality

Twitch iOS app streamers, check your broadcast setup against Apple's recommendations here: developer.apple.com/documentation

    Codec: AAC-LC. Stereo or Mono
    Recommended Bitrate (for maximum compatibility) 96kbps
    Maximum audio bit rate: 160 kbps (AAC)
    Sampling frequency: any (AAC)

 
Other Useful Info

The maximum broadcast length is 48 hours. This cannot be changed. Reconnection behavior may depend on encoder used.
NVIDIA NVENC Specs
1080p 60fps

Resolution: 1920x1080

Bitrate: 6000 kbps

Rate Control: CBR

Framerate: 60 or 50 fps

Keyframe Interval: 2 seconds

Preset: Quality

B-frames: 2
x264 Specs
1080p 60fps

Resolution: 1920x1080

Bitrate: 6000 kbps

Rate Control: CBR

Framerate: 60 or 50 fps

Keyframe Interval: 2 seconds

Preset: veryfast <-> medium

Profile: Main/High
1080p 30fps

Resolution: 1920x1080

Bitrate: 4500 kbps

Rate Control: CBR

Framerate: 25 or 30 fps

Keyframe Interval: 2 seconds

Preset: Quality

B-frames: 2
1080p 30fps

Resolution: 1980x1080

Bitrate: 4500 kbps

Rate Control: CBR

Framerate: 25 or 30 fps

Keyframe Interval: 2 seconds

Preset: veryfast <-> medium

Profile: Main/High
720p 60fps

Resolution: 1280x720

Bitrate: 4500 kbps

Rate Control: CBR

Framerate: 60 or 50 fps

Keyframe Interval: 2 seconds

Preset: Quality

B-frames: 2
720p 60fps

Resolution: 1280x720

Bitrate: 4500 kbps

Rate Control: CBR

Framerate: 60 or 50 fps

Keyframe Interval: 2 seconds

Preset: veryfast <-> medium

Profile: Main/High
720p 30fps

Resolution: 1280x720

Bitrate: 3000 kbps

Rate Control: CBR

Framerate: 25 or 30 fps

Keyframe Interval: 2 seconds

Preset: Quality

B-frames: 2
720p 30fps

Resolution: 1280x720

Bitrate: 3000 kbps

Rate Control: CBR

Framerate: 25 or 30 fps

Keyframe Interval: 2 seconds

Preset: veryfast <-> medium

Profile: Main/High
