#!/bin/zsh
# One-off: assemble the 36s pitch video from Veo clips + real Lumina outputs + Gemini TTS VO + Lyria music.
# Timeline (s): A 0-8.62 | B -17.38 | C -23.63 | REAL -27.48 | ENDCARD -36. xfade 0.6 centered on cuts.
# VO starts: 0.8 / 8.92 / 17.68 / 23.88 / 27.78. Titles are Pillow-rendered PNGs (homebrew ffmpeg
# has no drawtext), overlaid with alpha fades. Run: ./pitch_assemble.sh
set -euo pipefail
cd "$(dirname "$0")"

P=outputs/pitch
OUT=$P/lumina_pitch.mp4

ffmpeg -y -v error \
  -i $P/a_orchestration_1080.mp4 \
  -i $P/b_cloud_1080.mp4 \
  -i $P/c_market_1080.mp4 \
  -i $P/d_endcard_1080.mp4 \
  -i outputs/product_video.mp4 \
  -i outputs/v2/macro.mp4 \
  -i outputs/prod/card1.png \
  -i outputs/rings/card1.png \
  -i outputs/v2/card1.png \
  -i outputs/prod/card2.png \
  -i $P/music.wav \
  -i $P/vo_1_t.wav -i $P/vo_2_t.wav -i $P/vo_3a_t.wav -i $P/vo_3b_t.wav -i $P/vo_4_t.wav \
  -loop 1 -t 6.7 -r 30 -i $P/t1.png \
  -loop 1 -t 7.5 -r 30 -i $P/t2.png \
  -loop 1 -t 5.2 -r 30 -i $P/t3.png \
  -loop 1 -t 3.1 -r 30 -i $P/t4.png \
  -loop 1 -t 7.6 -r 30 -i $P/t5.png \
  -loop 1 -t 6.4 -r 30 -i $P/t6.png \
  -loop 1 -t 5.4 -r 30 -i $P/t7.png \
  -filter_complex "
[0:v]setpts=PTS*1.115,fps=30,setsar=1,settb=AVTB[va];
[1:v]setpts=PTS*1.17,fps=30,setsar=1,settb=AVTB[vb];
[2:v]trim=0:6.85,setpts=PTS-STARTPTS,fps=30,setsar=1,settb=AVTB[vc];
[3:v]setpts=PTS*1.1025,fps=30,setsar=1,settb=AVTB[vd];
color=c=0x0a0e18:s=1920x1080:r=30:d=4.45[rbg];
[4:v]trim=1.2:5.65,setpts=PTS-STARTPTS,scale=484:860,drawbox=x=0:y=0:w=iw:h=ih:color=white@0.22:t=2,setsar=1[pv];
[5:v]trim=1.0:5.45,setpts=PTS-STARTPTS,crop=720:1080:0:110,scale=484:860,eq=brightness=0.06:saturation=1.1,drawbox=x=0:y=0:w=iw:h=ih:color=white@0.22:t=2,setsar=1[mv];
[6:v]scale=335:416[c1];
[7:v]scale=335:416[c2];
[8:v]scale=335:416[c3];
[9:v]scale=335:416[c4];
[rbg][pv]overlay=72:110[r1];
[r1][mv]overlay=1364:110[r2];
[r2][c1]overlay=613:112[r3];
[r3][c2]overlay=972:112[r4];
[r4][c3]overlay=613:552[r5];
[r5][c4]overlay=972:552,format=yuv420p,setsar=1,settb=AVTB[vr];
[va][vb]xfade=transition=fade:duration=0.6:offset=8.32[x1];
[x1][vc]xfade=transition=fade:duration=0.6:offset=17.08[x2];
[x2][vr]xfade=transition=fade:duration=0.6:offset=23.33[x3];
[x3][vd]xfade=transition=fade:duration=0.6:offset=27.18[xv];
[16:v]format=rgba,fade=t=in:st=0:d=0.5:alpha=1,fade=t=out:st=6.2:d=0.5:alpha=1,setpts=PTS+1.4/TB[o1];
[17:v]format=rgba,fade=t=in:st=0:d=0.5:alpha=1,fade=t=out:st=7.0:d=0.5:alpha=1,setpts=PTS+9.4/TB[o2];
[18:v]format=rgba,fade=t=in:st=0:d=0.5:alpha=1,fade=t=out:st=4.7:d=0.5:alpha=1,setpts=PTS+18.0/TB[o3];
[19:v]format=rgba,fade=t=in:st=0:d=0.5:alpha=1,fade=t=out:st=2.6:d=0.5:alpha=1,setpts=PTS+24.0/TB[o4];
[20:v]format=rgba,fade=t=in:st=0:d=0.7:alpha=1,setpts=PTS+28.4/TB[o5];
[21:v]format=rgba,fade=t=in:st=0:d=0.7:alpha=1,setpts=PTS+29.6/TB[o6];
[22:v]format=rgba,fade=t=in:st=0:d=0.7:alpha=1,setpts=PTS+30.6/TB[o7];
[xv][o1]overlay=0:0:eof_action=pass[y1];
[y1][o2]overlay=0:0:eof_action=pass[y2];
[y2][o3]overlay=0:0:eof_action=pass[y3];
[y3][o4]overlay=0:0:eof_action=pass[y4];
[y4][o5]overlay=0:0:eof_action=pass[y5];
[y5][o6]overlay=0:0:eof_action=pass[y6];
[y6][o7]overlay=0:0:eof_action=pass[y7];
[y7]fade=t=in:st=0:d=0.8,fade=t=out:st=35.2:d=0.8[vout];
[10:a]atempo=0.918,afade=t=in:d=1.8,afade=t=out:st=33.5:d=2.2,volume=0.23,aresample=48000[am];
[11:a]aresample=48000,pan=stereo|c0=c0|c1=c0,volume=0.9,adelay=800|800[w1];
[12:a]aresample=48000,pan=stereo|c0=c0|c1=c0,volume=0.9,adelay=8920|8920[w2];
[13:a]aresample=48000,pan=stereo|c0=c0|c1=c0,volume=0.9,adelay=17680|17680[w3];
[14:a]aresample=48000,pan=stereo|c0=c0|c1=c0,volume=0.9,adelay=23880|23880[w4];
[15:a]aresample=48000,pan=stereo|c0=c0|c1=c0,volume=0.9,adelay=27780|27780[w5];
[am][w1][w2][w3][w4][w5]amix=inputs=6:normalize=0:duration=longest,alimiter=limit=0.95[aout]
" \
  -map "[vout]" -map "[aout]" -t 36 \
  -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p -r 30 \
  -c:a aac -b:a 192k -movflags +faststart \
  "$OUT"

echo "---"
ffprobe -v error -show_entries "format=duration,size:stream=codec_name,width,height" -of csv=p=0 "$OUT"
