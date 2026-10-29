// SPDX-License-Identifier: GPL-3.0-or-later
// Standalone experimental receiver by PU5MFH.
// FEC, Gray mapping, deinterleaving and CRC adapted from EPFL gr-lora_sdr,
// Joachim Tapparel et al., upstream revision 862746dd1cf635c9c8a4bfbaa2c3a0ec3a5306c9.
// Synchronization and streaming front-end below are a separate simplified implementation,
// not a port of EPFL frame_sync (no SFO tracking or fractional STO estimator yet).
#include <algorithm>
#include <array>
#include <cmath>
#include <chrono>
#include <complex>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <iomanip>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>
#include "kiss_fft.h"
#include "tables.h"
using C=std::complex<float>;
constexpr int N=256, OS=4, NS=N*OS;
constexpr double BW=62500, RATE=BW*OS, PI=3.14159265358979323846;
int mod(int a,int b){return (a%b+b)%b;}
int signed_bin(int a){return a>=N/2?a-N:a;}
C ph(double a){return C(std::cos(a),std::sin(a));}
struct FFT {
 kiss_fft_cfg cfg=kiss_fft_alloc(N,0,nullptr,nullptr);
 std::array<kiss_fft_cpx,N> in{},out{};
 std::array<C,N> refs{};
 FFT(){
  if(!cfg)throw std::runtime_error("FFT allocation failed");
  for(int i=0;i<N;i++){double t=i;refs[i]=ph(2*PI*(t*t/(2*N)-t/2));}
 }
 ~FFT(){free(cfg);}
 std::array<double,N> powers(const std::vector<C>& x,size_t p,bool down,double cfo=0) {
  for(int i=0;i<N;i++){
   C ref=refs[i];
   C v=x[p+i*OS]*(down?ref:std::conj(ref));
   if(cfo!=0)v*=ph(-2*PI*cfo*i/N);
   in[i]={v.real(),v.imag()};
  }
  kiss_fft(cfg,in.data(),out.data());
  std::array<double,N> e{};
  for(int i=0;i<N;i++)e[i]=double(out[i].r)*out[i].r+double(out[i].i)*out[i].i;
  return e;
 }
};
int peak(const std::array<double,N>& e){return std::max_element(e.begin(),e.end())-e.begin();}
double strength(const std::array<double,N>& e){double sum=0;for(double v:e)sum+=v;return sum>0?e[peak(e)]/sum:0;}
using LLR=std::array<double,8>;
// EPFL max-log alternative using FFT energy metrics. All 256 hypotheses retained.
LLR llrs(const std::array<double,N>& e,bool header){
 LLR v{};
 for(int b=0;b<8;b++){
  double zero=-std::numeric_limits<double>::infinity(),one=zero;
  for(int k=0;k<N;k++){
   int s=mod(k-1,N)/(header?4:1);s^=s>>1;
   if(s&(1<<b))one=std::max(one,e[k]);else zero=std::max(zero,e[k]);
  }
  v[7-b]=(std::isfinite(one)&&std::isfinite(zero))?one-zero:0;
 }
 return v;
}
const uint8_t codewords[16]={0,23,45,58,78,89,99,116,139,156,166,177,197,210,232,255};
const uint8_t cr5words[16]={0,24,40,48,72,80,96,120,136,144,160,184,192,216,232,240};
uint8_t reverse4(uint8_t x){return ((x&1)<<3)|((x&2)<<1)|((x&4)>>1)|((x&8)>>3);}
std::vector<uint8_t> fec(const std::vector<LLR>& symbols,bool header,int cr){
 int sf=header?6:8;
 std::vector<std::array<double,8>> deint(sf);
 for(int i=0;i<cr;i++)for(int j=0;j<sf;j++)deint[mod(i-j-1,sf)][i]=symbols[i][8-sf+j];
 std::vector<uint8_t> out;
 for(auto& cw:deint){
  double best=-std::numeric_limits<double>::infinity();int chosen=0;
  for(int n=0;n<16;n++){
   int bits=(cr==5?cr5words[n]:codewords[n])>>(8-cr);double score=0;
   for(int j=0;j<cr;j++)score+=((bits>>(cr-1-j))&1)?cw[j]:-cw[j];
   if(score>best){best=score;chosen=n;}
  }
  out.push_back(reverse4(codewords[chosen]>>4));
 }
 return out;
}
bool header_ok(const std::vector<uint8_t>& h){
 if(h.size()<5)return false;
 auto b=[&](int i,int n){return (h[i]>>n)&1;};
 int c4=b(0,3)^b(0,2)^b(0,1)^b(0,0);
 int c3=b(0,3)^b(1,3)^b(1,2)^b(1,1)^b(2,0);
 int c2=b(0,2)^b(1,3)^b(1,0)^b(2,3)^b(2,1);
 int c1=b(0,1)^b(1,2)^b(1,0)^b(2,2)^b(2,1)^b(2,0);
 int c0=b(0,0)^b(1,1)^b(2,3)^b(2,2)^b(2,1)^b(2,0);
 return (((h[3]&1)<<4)|h[4])==((c4<<4)|(c3<<3)|(c2<<2)|(c1<<1)|c0);
}
uint16_t crc16(const std::vector<uint8_t>& x,size_t len){
 uint16_t crc=0;for(size_t n=0;n<len;n++)for(int bit=7;bit>=0;bit--)crc=(crc<<1)^((((crc>>15)^((x[n]>>bit)&1))&1)?0x1021:0);
 return crc;
}
std::string hex(const std::vector<uint8_t>& b,size_t n){const char* h="0123456789abcdef";std::string s;for(size_t i=0;i<n;i++){s+=h[b[i]>>4];s+=h[b[i]&15];}return s;}
struct Decoder {
 FFT fft;
 std::vector<C> x;
 size_t scan=0;uint64_t base=0,id=0,headers=0,valid=0;
 uint64_t windows=0,preambles=0,sfds=0,syncs=0,head_trials=0,head_ok=0;
 double frequency;
 explicit Decoder(double f):frequency(f){}
 // Return 0 while waiting for more IQ, 1 when examined/rejected.
 int attempt(size_t start,double cfo){
  if(start+8*NS>=x.size())return 0;
  std::vector<LLR> symbols;
  for(int i=0;i<8;i++)symbols.push_back(llrs(fft.powers(x,start+i*NS,false,cfo),true));
  head_trials++;
  auto h=fec(symbols,true,8);
  if(!header_ok(h))return 1;
  head_ok++;
  int len=h[0]*16+h[1],cr=(h[2]>>1)+4;bool hascrc=h[2]&1;
  if(len<2||len>255||cr<5||cr>8||!hascrc)return 1;
  size_t need=(len+2)*2;
  size_t blocks=(need-1+7)/8; // header block already carries one payload nibble at SF8
  if(start+(8+blocks*cr)*NS>=x.size())return 0;
  std::vector<uint8_t> nibbles(h.begin()+5,h.end());
  for(size_t b=0;b<blocks;b++){
   symbols.clear();
   for(int j=0;j<cr;j++)symbols.push_back(llrs(fft.powers(x,start+(8+b*cr+j)*NS,false,cfo),false));
   auto part=fec(symbols,false,cr);nibbles.insert(nibbles.end(),part.begin(),part.end());
  }
  std::vector<uint8_t> bytes;
  for(size_t i=0;i<size_t(len+2);i++){
   uint8_t v=nibbles[2*i]|(nibbles[2*i+1]<<4);
   if(i<size_t(len))v^=gr::lora_sdr::whitening_seq[i];
   bytes.push_back(v);
  }
  uint16_t check=crc16(bytes,len-2)^bytes[len-1]^(bytes[len-2]<<8);
  bool ok=check==(bytes[len]|(bytes[len+1]<<8));headers++;valid+=ok;
  std::cout<<"{\"decoder\":\"epfl-derived-standalone\",\"id\":"<<++id
   <<",\"sample_index\":"<<base+start<<",\"sample_rate\":250000,\"frequency_hz\":"<<std::fixed<<std::setprecision(0)<<frequency
   <<",\"sf\":8,\"bw\":62500,\"sync_word\":\"0x12\",\"cr\":"<<cr
   <<",\"crc\":"<<(ok?1:0)<<",\"afc_hz\":"<<std::setprecision(2)<<cfo*BW/N
   <<",\"payload_hex\":\""<<hex(bytes,len)<<"\"}"<<std::endl;
  return 1;
 }
 void process(){
  while(scan+7*NS<x.size()){
   windows++;
   auto a=fft.powers(x,scan,false);int k=peak(a);
   if(strength(a)==0){scan+=NS;continue;}
   int count=1;
   for(int n=1;n<5;n++){
    auto b=fft.powers(x,scan+n*NS,false);
    if(std::abs(signed_bin(mod(peak(b)-k,N)))<=1&&strength(b)>0)count++;else break;
   }
   if(count<5){scan+=NS;continue;}
   // Search sync and SFD after the detected preamble; support extended preambles.
   preambles++;
   bool wait=false,done=false;
   for(int n=5;n<80;n++){
    size_t pos=scan+n*NS;
    if(pos+4*NS>=x.size()){wait=true;break;}
    auto d=fft.powers(x,pos,true);
    // Do not impose a peak/total-energy cutoff here: weak SFD chirps
    // can be below the former arbitrary 6% gate. Confirm via sync + header.
    int kd=peak(d);
    auto d2=fft.powers(x,pos+NS,true);
    if(std::abs(signed_bin(mod(peak(d2)-kd,N)))>1)continue;
    sfds++;
    // Up/down peaks separate timing offset from CFO. Resolve modulo ambiguity
    // by requiring CFO within BW/4 (the channel passband).
    int sum=signed_bin(mod(k+kd,N));double cfo=sum/2.0;
    int timing=mod(int(std::lround(k-cfo)),N);
    size_t downstart=pos-size_t(timing)*OS;
    if(downstart<2*NS)continue;
    // Refine fractional CFO using phase progression of repeated preamble samples.
    C corr{};for(int j=0;j<NS;j++)corr+=std::conj(x[scan+j])*x[scan+NS+j];
    double frac=std::arg(corr)/(2*PI);cfo=std::round(cfo-frac)+frac;
    // The coarse downchirp window can start before or after the true
    // boundary. Resolve the one-symbol ambiguity using both sync chirps.
    bool sync_ok=false;
    for(int extra=0;extra<2;extra++){
     size_t candidate=downstart+extra*NS;
     auto s1=fft.powers(x,candidate-2*NS,false,cfo);
     auto s2=fft.powers(x,candidate-NS,false,cfo);
     if(std::abs(signed_bin(mod(peak(s1)-8,N)))<=2&&std::abs(signed_bin(mod(peak(s2)-16,N)))<=2){
      downstart=candidate;sync_ok=true;break;
     }
    }
    if(!sync_ok)continue;
    syncs++;
    size_t payload=downstart+2*NS+NS/4;
    if(!attempt(payload,cfo)){wait=true;break;}
    scan=payload;done=true;break;
   }
   if(wait)break;
   if(!done)scan+=NS;
  }
  if(scan>128*NS){x.erase(x.begin(),x.begin()+scan);base+=scan;scan=0;}
 }
 void diagnostics(std::ostream& out){
  out<<"{\"event\":\"diagnostics\",\"windows\":"<<windows<<",\"preamble_attempts\":"<<preambles
     <<",\"sfd_attempts\":"<<sfds<<",\"sync_attempts\":"<<syncs<<",\"header_trials\":"<<head_trials
     <<",\"header_checks_passed\":"<<head_ok<<",\"payloads\":"<<headers<<",\"crc_valid\":"<<valid<<"}"<<std::endl;
 }
 void feed(C v){x.push_back(v);if(x.size()%4096==0)process();}
};
// Band-limited arbitrary-rate CF32 front-end. Output 250 kS/s (4*BW).
// Bounded history, continuous NCO, windowed-sinc filter; no hardware assumptions.
struct Frontend {
 double rate,offset,phase=0,step,next=32;uint64_t base=0;
 std::vector<C> data;Decoder& rx;
 std::array<std::array<float,64>,1024> kernels{};
 Frontend(double r,double o,Decoder& d):rate(r),offset(o),step(r/RATE),rx(d){
  const double fc=40000/rate;
  for(int phase=0;phase<1024;phase++){
   double norm=0;
   for(int j=-31;j<=32;j++){
    double t=j-double(phase)/1024;
    double w=(std::abs(t)<1e-10?2*fc:std::sin(2*PI*fc*t)/(PI*t))*(0.5+0.5*std::cos(PI*t/32));
    kernels[phase][j+31]=float(w);norm+=w;
   }
   for(auto& w:kernels[phase])w/=float(norm);
  }
 }
 void feed(C v){
  v*=ph(phase);phase=std::remainder(phase-2*PI*offset/rate,2*PI);data.push_back(v);
  constexpr int taps=64;
  while(next+taps/2<base+data.size()){
   int64_t mid=int64_t(std::floor(next));
   int phase_index=std::min(1023,int((next-mid)*1024));
   const auto& weights=kernels[phase_index];
   float real=0,imag=0;
   size_t first=size_t(mid-31-base);
   for(int j=0;j<64;j++){
    real+=data[first+j].real()*weights[j];
    imag+=data[first+j].imag()*weights[j];
   }
   rx.feed(C(real,imag));next+=step;
  }
  if(data.size()>4096){size_t remove=size_t(std::floor(next))-base-32;data.erase(data.begin(),data.begin()+remove);base+=remove;}
 }
};
int main(int argc,char** argv){
 try{
  double rate=500000,center=923250000,freq=923125000;
  for(int i=1;i<argc;i++){
   std::string a=argv[i];
   if(a=="--help"){std::cout<<"meshcore-epfl-rx --sample-rate HZ --center-hz HZ --frequency-hz HZ\nInput: native little-endian CF32 IQ on stdin; output: JSONL stdout. SF8/BW62500/explicit header/CRC, soft FEC. Experimental synchronization.\n";return 0;}
   if(i+1==argc)throw std::runtime_error("missing option value");
   double v=std::stod(argv[++i]);if(a=="--sample-rate")rate=v;else if(a=="--center-hz")center=v;else if(a=="--frequency-hz")freq=v;else throw std::runtime_error("unknown option: "+a);
  }
  if(!std::isfinite(rate)||!std::isfinite(center)||!std::isfinite(freq)||rate<125000||rate>10000000||std::abs(freq-center)+40000>=rate/2)throw std::runtime_error("invalid IQ rate/channel bandwidth");
  Decoder rx(freq);Frontend front(rate,freq-center,rx);
  auto reported=std::chrono::steady_clock::now();
  std::array<char,65536> raw{};size_t held=0;
  while(std::cin){std::cin.read(raw.data()+held,raw.size()-held);size_t n=held+std::cin.gcount();size_t whole=n/8*8;
   for(size_t i=0;i<whole;i+=8){float iq[2];std::memcpy(iq,raw.data()+i,8);if(!std::isfinite(iq[0])||!std::isfinite(iq[1]))throw std::runtime_error("nonfinite IQ");front.feed(C(iq[0],iq[1]));}
   if(std::chrono::steady_clock::now()-reported>std::chrono::seconds(60)){
    rx.diagnostics(std::cout);reported=std::chrono::steady_clock::now();
   }
   held=n-whole;std::memmove(raw.data(),raw.data()+whole,held);
  }
  if(held)throw std::runtime_error("truncated CF32 sample");
  rx.process();
  rx.diagnostics(std::cerr);
  std::cerr<<"headers="<<rx.headers<<" crc_valid="<<rx.valid<<"\n";
 }catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}
}
