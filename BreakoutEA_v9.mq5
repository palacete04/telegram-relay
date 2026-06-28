//+------------------------------------------------------------------+
//|  BreakoutEA_v8.mq5                                               |
//|  v8: Alertas Telegram + Max 1 operación abierta                  |
//+------------------------------------------------------------------+
#property copyright "BreakoutEA v8"
#property version   "9.03"
#property strict

#include <Trade\Trade.mqh>

//--- Parámetros generales
input double   LotSize             = 0.01;
input int      ServerOffset        = 1;
input bool     ModoDemo            = false;

//--- Telegram
input string   TelegramToken       = "8957492846:AAGophSxXOSZGT4Gd1cLTNOICzxpZIH5wEU";
input string   TelegramChatID      = "6518133529";

//--- Estrategia 1: Nasdaq
input bool     UsarEstrategia1     = true;
input int      NasdaqEntryHour     = 9;
input int      NasdaqEntryMinute   = 45;
input double   NasdaqRatioTP       = 1.0;
input double   NasdaqRatioSL       = 0.5;

//--- Estrategia 2: Europa
input bool     UsarEstrategia2     = true;
input int      EuropaRangeHour     = 3;
input int      EuropaEntryHour     = 4;
input double   EuropaRatioTP       = 1.0;
input double   EuropaRatioSL       = 0.5;

//--- Estrategia 3: Tokyo
input bool     UsarEstrategia3     = true;
input int      TokyoRangeHour      = 19;
input int      TokyoEntryHour      = 20;
input double   TokyoRatioTP        = 1.0;
input double   TokyoRatioSL        = 0.5;

//--- Estrategia 4: RSI
input bool     UsarRSI             = true;
input int      RSIPeriod           = 14;
input double   RSISobrevendido     = 25.0;
input double   RSISobrecomprado    = 75.0;
input double   RSITPPips           = 30.0;
input double   RSISLPips           = 15.0;

//--- Estrategia 5: Bollinger
input bool     UsarBollinger       = true;
input int      BollingerPeriod     = 20;
input double   BollingerDesviacion = 2.5;
input double   BollTPPips          = 25.0;
input double   BollSLPips          = 12.0;

//--- Filtros
input bool     UsarFiltroTendencia = true;
input int      MA200Period         = 200;
input int      MA50Period          = 50;
input bool     UsarFiltroRango     = true;
input double   RangoMinPips        = 20.0;
input double   RangoMinEuropa      = 7.0;
input double   RangoMinTokyo       = 5.0;

//--- Trailing
input bool     UsarTrailingStop    = true;
input double   TrailingPips        = 15.0;
input double   TrailingStartPips   = 10.0;

//--- Cierre
input int      CloseHour           = 16;

//--- Variables Nasdaq
double prevHigh=0,prevLow=0;
bool   levelsSet=false,tradedNasdaq=false,tradeNasdaqOpen=false;
ulong  ticketNasdaq=0;

//--- Variables Europa
double europaHigh=0,europaLow=0;
bool   europaRangeSet=false,tradedEuropa=false,tradeEuropaOpen=false;
ulong  ticketEuropa=0;

//--- Variables Tokyo
double tokyoHigh=0,tokyoLow=0;
bool   tokyoRangeSet=false,tradedTokyo=false,tradeTokyoOpen=false;
ulong  ticketTokyo=0;
int    lastTokyoDay=-1;

//--- Variables RSI
bool     tradeRSIOpen=false;
ulong    ticketRSI=0;
datetime lastRSIBar=0;

//--- Variables Bollinger
bool     tradeBollOpen=false;
ulong    ticketBoll=0;
datetime lastBollBar=0;

//--- Global
datetime lastDay=0;
datetime lastHeartbeat=0;
CTrade trade;

//+------------------------------------------------------------------+
//| Enviar mensaje a Telegram                                        |
//+------------------------------------------------------------------+
void SendTelegram(string mensaje)
{
   string url = "https://telegram-relay-6x6l.onrender.com/notify";
   string body = "{\"message\":\"" + mensaje + "\"}";
   char post[], result[];
   string headers = "Content-Type: application/json\r\n";
   StringToCharArray(body, post, 0, StringLen(body));
   string responseHeaders;
   
   int maxRetries = 3;
   int retryDelay = 5000; // 5 segundos
   
   for(int attempt = 1; attempt <= maxRetries; attempt++)
   {
      ArrayResize(result, 0);
      int res = WebRequest("POST", url, headers, 5000, post, result, responseHeaders);
      if(res != -1)
      {
         Print("Telegram enviado OK (intento ", attempt, ")");
         return;
      }
      Print("Error Telegram intento ", attempt, "/", maxRetries, ": ", GetLastError());
      if(attempt < maxRetries)
         Sleep(retryDelay);
   }
   Print("Telegram: todos los intentos fallaron. Bot sigue operando normalmente.");
}

void SendTradeData(string strategy, string type, double entry, double exitPrice, double profit, double sl, double tp)
{
   string url = "https://telegram-relay-6x6l.onrender.com/trade";
   string body = "{\"strategy\":\"" + strategy + "\"," +
                 "\"type\":\"" + type + "\"," +
                 "\"entry\":" + DoubleToString(entry, 5) + "," +
                 "\"exit\":" + DoubleToString(exitPrice, 5) + "," +
                 "\"profit\":" + DoubleToString(profit, 2) + "," +
                 "\"sl\":" + DoubleToString(sl, 5) + "," +
                 "\"tp\":" + DoubleToString(tp, 5) + "}";
   char post[], result[];
   string headers = "Content-Type: application/json\r\n";
   StringToCharArray(body, post, 0, StringLen(body));
   string responseHeaders;
   
   int maxRetries = 3;
   int retryDelay = 5000;
   
   for(int attempt = 1; attempt <= maxRetries; attempt++)
   {
      ArrayResize(result, 0);
      int res = WebRequest("POST", url, headers, 5000, post, result, responseHeaders);
      if(res != -1)
      {
         Print("Trade data enviado OK (intento ", attempt, ")");
         return;
      }
      Print("Error SendTradeData intento ", attempt, "/", maxRetries, ": ", GetLastError());
      if(attempt < maxRetries)
         Sleep(retryDelay);
   }
   Print("SendTradeData: todos los intentos fallaron.");
}

//+------------------------------------------------------------------+
//| Verificar si hay operación abierta (cualquier estrategia)        |
//+------------------------------------------------------------------+
bool HayOperacionAbierta()
{
   return tradeNasdaqOpen || tradeEuropaOpen || tradeTokyoOpen || tradeRSIOpen || tradeBollOpen;
}

//+------------------------------------------------------------------+
int OnInit()
{
   Print("=== BreakoutEA v9.3 iniciado ===");
   Print("E1 Nasdaq:    ",UsarEstrategia1?"ACTIVA":"INACTIVA");
   Print("E2 Europa:    ",UsarEstrategia2?"ACTIVA":"INACTIVA");
   Print("E3 Tokyo:     ",UsarEstrategia3?"ACTIVA":"INACTIVA");
   Print("E4 RSI:       ",UsarRSI?"ACTIVA":"INACTIVA");
   Print("E5 Bollinger: ",UsarBollinger?"ACTIVA":"INACTIVA");
   Print("Modo demo:    ",ModoDemo?"SI":"NO");
   Print("Telegram:     ACTIVO");
   trade.SetExpertMagicNumber(202509);
   SendTelegram("BreakoutEA v9 iniciado correctamente en " + Symbol());
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnTick()
{
   datetime now=TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(now,dt);
   int hourET=(dt.hour-ServerOffset+24)%24;

   //--- Heartbeat cada 30 minutos
   if(TimeCurrent() - lastHeartbeat >= 1800)
   {
      SendHeartbeat();
      lastHeartbeat = TimeCurrent();
   }

   //--- Reset diario
   if(dt.day!=TimeDay(lastDay))
   {
      lastDay=now;
      levelsSet=false; tradedNasdaq=false; tradeNasdaqOpen=false; ticketNasdaq=0;
      europaRangeSet=false; tradedEuropa=false; tradeEuropaOpen=false; ticketEuropa=0;
      prevHigh=prevLow=europaHigh=europaLow=0;
      Print("--- Nuevo día ---");
      SendTelegram("[DIA] Nuevo día iniciado - " + Symbol());
   }

   //--- Reset Tokyo
   if(hourET==TokyoRangeHour && dt.min==0 && lastTokyoDay!=dt.day)
   {
      tokyoRangeSet=false; tradedTokyo=false; tradeTokyoOpen=false; ticketTokyo=0;
      tokyoHigh=tokyoLow=0;
   }

   //--- Niveles Nasdaq
   if(!levelsSet)
   {
      double dH[1],dL[1];
      if(CopyHigh(Symbol(),PERIOD_D1,1,1,dH)==1&&CopyLow(Symbol(),PERIOD_D1,1,1,dL)==1)
      {
         prevHigh=dH[0]; prevLow=dL[0]; levelsSet=true;
         double r=(prevHigh-prevLow)/_Point/10;
         Print("[E1-Nasdaq] H:",prevHigh," L:",prevLow," R:",DoubleToString(r,1),"p");
         if(UsarFiltroRango&&r<RangoMinPips)
         {
            Print("[AVISO] [E1] Rango insuf."); tradedNasdaq=true;
            SendTelegram("[AVISO] Nasdaq: rango insuficiente hoy (" + DoubleToString(r,1) + " pips)");
         }
      }
      return;
   }

   //--- Rango Europa
   if(UsarEstrategia2&&!europaRangeSet&&hourET==EuropaRangeHour&&dt.min==0)
   {
      double h[1],l[1];
      if(CopyHigh(Symbol(),PERIOD_H1,1,1,h)==1&&CopyLow(Symbol(),PERIOD_H1,1,1,l)==1)
      {
         europaHigh=h[0]; europaLow=l[0]; europaRangeSet=true;
         double r=(europaHigh-europaLow)/_Point/10;
         Print("[E2-Europa] H:",europaHigh," L:",europaLow," R:",DoubleToString(r,1),"p");
         if(UsarFiltroRango&&r<RangoMinEuropa)
         {
            Print("[AVISO] [E2] Rango insuf."); tradedEuropa=true;
            SendTelegram("[AVISO] Europa: rango insuficiente (" + DoubleToString(r,1) + " pips)");
         }
      }
   }

   //--- Rango Tokyo
   if(UsarEstrategia3&&!tokyoRangeSet&&hourET==TokyoRangeHour&&dt.min==0&&lastTokyoDay!=dt.day)
   {
      double h[1],l[1];
      if(CopyHigh(Symbol(),PERIOD_H1,1,1,h)==1&&CopyLow(Symbol(),PERIOD_H1,1,1,l)==1)
      {
         tokyoHigh=h[0]; tokyoLow=l[0]; tokyoRangeSet=true; lastTokyoDay=dt.day;
         double r=(tokyoHigh-tokyoLow)/_Point/10;
         Print("[E3-Tokyo] H:",tokyoHigh," L:",tokyoLow," R:",DoubleToString(r,1),"p");
         if(UsarFiltroRango&&r<RangoMinTokyo)
         {
            Print("[AVISO] [E3] Rango insuf."); tradedTokyo=true;
            SendTelegram("[AVISO] Tokyo: rango insuficiente (" + DoubleToString(r,1) + " pips)");
         }
      }
   }

   //--- Trailing
   if(UsarTrailingStop&&!ModoDemo)
   {
      if(tradeNasdaqOpen&&ticketNasdaq>0) GestionarTrailing(ticketNasdaq,"Nasdaq");
      if(tradeEuropaOpen&&ticketEuropa>0) GestionarTrailing(ticketEuropa,"Europa");
      if(tradeTokyoOpen&&ticketTokyo>0)   GestionarTrailing(ticketTokyo,"Tokyo");
      if(tradeRSIOpen&&ticketRSI>0)       GestionarTrailing(ticketRSI,"RSI");
      if(tradeBollOpen&&ticketBoll>0)     GestionarTrailing(ticketBoll,"Bollinger");
   }

   //--- Cierre forzado
   if(hourET>=CloseHour)
   {
      if(tradeNasdaqOpen) CerrarPosicion(ticketNasdaq,"Cierre Nasdaq");
      if(tradeEuropaOpen) CerrarPosicion(ticketEuropa,"Cierre Europa");
      return;
   }

   //--- MA200 y MA50
   double ma200=0,ma50=0;
   if(UsarFiltroTendencia)
   {
      double mb200[1],mb50[1];
      if(CopyBuffer(iMA(Symbol(),PERIOD_H1,MA200Period,0,MODE_EMA,PRICE_CLOSE),0,0,1,mb200)==1)
         ma200=mb200[0];
      if(CopyBuffer(iMA(Symbol(),PERIOD_H1,MA50Period,0,MODE_EMA,PRICE_CLOSE),0,0,1,mb50)==1)
         ma50=mb50[0];
   }

   double bid=SymbolInfoDouble(Symbol(),SYMBOL_BID);
   double ask=SymbolInfoDouble(Symbol(),SYMBOL_ASK);

   bool tendenciaAlcista=(ma50>0&&ma200>0&&ask>ma50&&ma50>ma200);
   bool tendenciaBajista=(ma50>0&&ma200>0&&bid<ma50&&ma50<ma200);

   //=================================================================
   //--- E4: RSI — máximo 1 operación abierta
   //=================================================================
   if(UsarRSI&&!tradeRSIOpen&&!HayOperacionAbierta())
   {
      datetime barTime=iTime(Symbol(),PERIOD_H1,0);
      if(barTime!=lastRSIBar)
      {
         int rsiHandle=iRSI(Symbol(),PERIOD_H1,RSIPeriod,PRICE_CLOSE);
         double rsiBuffer[1];
         if(rsiHandle!=INVALID_HANDLE&&CopyBuffer(rsiHandle,0,1,1,rsiBuffer)==1)
         {
            double rsi=rsiBuffer[0];
            double tpDist=RSITPPips*_Point*10;
            double slDist=RSISLPips*_Point*10;

            if(rsi<RSISobrevendido&&tendenciaAlcista)
            {
               double tp=ask+tpDist,sl=ask-slDist;
               Print(">>> [E4-RSI] BUY | RSI:",DoubleToString(rsi,1)," Ask:",ask);
               if(!ModoDemo)
               {
                  if(trade.Buy(LotSize,Symbol(),ask,sl,tp,"RSI BUY v8"))
                  {
                     ticketRSI=trade.ResultOrder(); tradeRSIOpen=true;
                     SendTelegram("[BUY] RSI BUY abierto\nEntrada: "+DoubleToString(ask,5)+"\nTP: "+DoubleToString(tp,5)+"\nSL: "+DoubleToString(sl,5));
                  }
               }
               lastRSIBar=barTime;
            }
            else if(rsi>RSISobrecomprado&&tendenciaBajista)
            {
               double tp=bid-tpDist,sl=bid+slDist;
               Print(">>> [E4-RSI] SELL | RSI:",DoubleToString(rsi,1)," Bid:",bid);
               if(!ModoDemo)
               {
                  if(trade.Sell(LotSize,Symbol(),bid,sl,tp,"RSI SELL v8"))
                  {
                     ticketRSI=trade.ResultOrder(); tradeRSIOpen=true;
                     SendTelegram("[SELL] RSI SELL abierto\nEntrada: "+DoubleToString(bid,5)+"\nTP: "+DoubleToString(tp,5)+"\nSL: "+DoubleToString(sl,5));
                  }
               }
               lastRSIBar=barTime;
            }
            IndicatorRelease(rsiHandle);
         }
      }
   }

   //=================================================================
   //--- E5: Bollinger — máximo 1 operación abierta
   //=================================================================
   if(UsarBollinger&&!tradeBollOpen&&!HayOperacionAbierta())
   {
      datetime barTime=iTime(Symbol(),PERIOD_H1,0);
      if(barTime!=lastBollBar)
      {
         int bollHandle=iBands(Symbol(),PERIOD_H1,BollingerPeriod,0,BollingerDesviacion,PRICE_CLOSE);
         double upperBand[1],lowerBand[1];
         if(bollHandle!=INVALID_HANDLE&&
            CopyBuffer(bollHandle,1,1,1,upperBand)==1&&
            CopyBuffer(bollHandle,2,1,1,lowerBand)==1)
         {
            double tpDist=BollTPPips*_Point*10;
            double slDist=BollSLPips*_Point*10;

            if(bid>upperBand[0]&&tendenciaBajista)
            {
               double tp=bid-tpDist,sl=bid+slDist;
               Print(">>> [E5-Boll] SELL | Bid:",bid);
               if(!ModoDemo)
               {
                  if(trade.Sell(LotSize,Symbol(),bid,sl,tp,"Boll SELL v8"))
                  {
                     ticketBoll=trade.ResultOrder(); tradeBollOpen=true;
                     SendTelegram("[SELL] Bollinger SELL abierto\nEntrada: "+DoubleToString(bid,5)+"\nTP: "+DoubleToString(tp,5)+"\nSL: "+DoubleToString(sl,5));
                  }
               }
               lastBollBar=barTime;
            }
            else if(ask<lowerBand[0]&&tendenciaAlcista)
            {
               double tp=ask+tpDist,sl=ask-slDist;
               Print(">>> [E5-Boll] BUY | Ask:",ask);
               if(!ModoDemo)
               {
                  if(trade.Buy(LotSize,Symbol(),ask,sl,tp,"Boll BUY v8"))
                  {
                     ticketBoll=trade.ResultOrder(); tradeBollOpen=true;
                     SendTelegram("[BUY] Bollinger BUY abierto\nEntrada: "+DoubleToString(ask,5)+"\nTP: "+DoubleToString(tp,5)+"\nSL: "+DoubleToString(sl,5));
                  }
               }
               lastBollBar=barTime;
            }
            IndicatorRelease(bollHandle);
         }
      }
   }

   //=================================================================
   //--- E3: Tokyo — máximo 1 operación abierta
   //=================================================================
   if(UsarEstrategia3&&tokyoRangeSet&&!tradedTokyo&&!tradeTokyoOpen&&
      hourET>=TokyoEntryHour&&!HayOperacionAbierta())
   {
      double range=tokyoHigh-tokyoLow;
      EjecutarBreakout(ask,bid,tokyoHigh,tokyoLow,range*TokyoRatioTP,range*TokyoRatioSL,
                       ma200,"E3-Tokyo","Tokyo BUY v8","Tokyo SELL v8",ticketTokyo,tradeTokyoOpen,tradedTokyo);
   }

   //=================================================================
   //--- E2: Europa — máximo 1 operación abierta
   //=================================================================
   if(UsarEstrategia2&&europaRangeSet&&!tradedEuropa&&!tradeEuropaOpen&&
      hourET>=EuropaEntryHour&&hourET<NasdaqEntryHour&&!HayOperacionAbierta())
   {
      double range=europaHigh-europaLow;
      EjecutarBreakout(ask,bid,europaHigh,europaLow,range*EuropaRatioTP,range*EuropaRatioSL,
                       ma200,"E2-Europa","Europa BUY v8","Europa SELL v8",ticketEuropa,tradeEuropaOpen,tradedEuropa);
   }

   //=================================================================
   //--- E1: Nasdaq — máximo 1 operación abierta
   //=================================================================
   if(UsarEstrategia1&&!tradedNasdaq&&!tradeNasdaqOpen&&
      (hourET>NasdaqEntryHour||(hourET==NasdaqEntryHour&&dt.min>=NasdaqEntryMinute))&&
      !HayOperacionAbierta())
   {
      double range=prevHigh-prevLow;
      EjecutarBreakout(ask,bid,prevHigh,prevLow,range*NasdaqRatioTP,range*NasdaqRatioSL,
                       ma200,"E1-Nasdaq","Nasdaq BUY v8","Nasdaq SELL v8",ticketNasdaq,tradeNasdaqOpen,tradedNasdaq);
   }
}

//+------------------------------------------------------------------+
void EjecutarBreakout(double ask,double bid,double nH,double nL,
                      double tpD,double slD,double ma200,string nom,
                      string lBuy,string lSell,ulong &tkt,bool &tOpen,bool &traded)
{
   if(ask>nH)
   {
      if(UsarFiltroTendencia&&ma200>0&&ask<ma200){Print("[AVISO] [",nom,"] BUY bloq"); traded=true; return;}
      double tp=ask+tpD,sl=ask-slD;
      Print(">>> [",nom,"] BUY | Ask:",ask," TP:",tp," SL:",sl);
      if(!ModoDemo)
      {
         if(trade.Buy(LotSize,Symbol(),ask,sl,tp,lBuy))
         {
            tkt=trade.ResultOrder(); tOpen=true; traded=true;
            SendTelegram("[BUY] "+nom+" BUY abierto\nEntrada: "+DoubleToString(ask,5)+"\nTP: "+DoubleToString(tp,5)+"\nSL: "+DoubleToString(sl,5));
         }
      }
      else{Print("[DEMO] ",nom," BUY @ ",ask); traded=true;}
   }
   else if(bid<nL)
   {
      if(UsarFiltroTendencia&&ma200>0&&bid>ma200){Print("[AVISO] [",nom,"] SELL bloq"); traded=true; return;}
      double tp=bid-tpD,sl=bid+slD;
      Print(">>> [",nom,"] SELL | Bid:",bid," TP:",tp," SL:",sl);
      if(!ModoDemo)
      {
         if(trade.Sell(LotSize,Symbol(),bid,sl,tp,lSell))
         {
            tkt=trade.ResultOrder(); tOpen=true; traded=true;
            SendTelegram("[SELL] "+nom+" SELL abierto\nEntrada: "+DoubleToString(bid,5)+"\nTP: "+DoubleToString(tp,5)+"\nSL: "+DoubleToString(sl,5));
         }
      }
      else{Print("[DEMO] ",nom," SELL @ ",bid); traded=true;}
   }
}

//+------------------------------------------------------------------+
void GestionarTrailing(ulong ticket,string nombre)
{
   if(!PositionSelectByTicket(ticket)) return;
   double tipo=PositionGetInteger(POSITION_TYPE);
   double open=PositionGetDouble(POSITION_PRICE_OPEN);
   double sl=PositionGetDouble(POSITION_SL);
   double bid=SymbolInfoDouble(Symbol(),SYMBOL_BID);
   double ask=SymbolInfoDouble(Symbol(),SYMBOL_ASK);
   double td=TrailingPips*_Point*10,ts=TrailingStartPips*_Point*10;
   if(tipo==POSITION_TYPE_BUY&&bid-open>=ts)
   { double nsl=bid-td; if(nsl>sl+_Point){trade.PositionModify(ticket,nsl,PositionGetDouble(POSITION_TP));} }
   else if(tipo==POSITION_TYPE_SELL&&open-ask>=ts)
   { double nsl=ask+td; if(nsl<sl-_Point||sl==0){trade.PositionModify(ticket,nsl,PositionGetDouble(POSITION_TP));} }
}

//+------------------------------------------------------------------+
void CerrarPosicion(ulong ticket,string motivo)
{
   if(ticket==0) return;
   if(PositionSelectByTicket(ticket)){trade.PositionClose(ticket); Print("Cerrado: ",motivo);}
}

//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction& trans,
                        const MqlTradeRequest& request,
                        const MqlTradeResult& result)
{
   if(trans.type==TRADE_TRANSACTION_DEAL_ADD)
   {
      ulong dealTicket=trans.deal;
      if(HistoryDealSelect(dealTicket))
      {
         long dealEntry=(long)HistoryDealGetInteger(dealTicket,DEAL_ENTRY);
         if(dealEntry==DEAL_ENTRY_OUT)
         {
            double dealProfit=HistoryDealGetDouble(dealTicket,DEAL_PROFIT);
            string emoji=dealProfit>=0?"✅":"❌";
            string msg=emoji+" Operación cerrada\nResultado: "+DoubleToString(dealProfit,2)+" USD";
            SendTelegram(msg);
            Print("Operación cerrada. P&L: ",dealProfit);
         }
      }
      if(trans.order==ticketNasdaq){tradeNasdaqOpen=false;}
      if(trans.order==ticketEuropa){tradeEuropaOpen=false;}
      if(trans.order==ticketTokyo) {tradeTokyoOpen=false;}
      if(trans.order==ticketRSI)   {tradeRSIOpen=false;}
      if(trans.order==ticketBoll)  {tradeBollOpen=false;}
   }
}

//+------------------------------------------------------------------+
void SendHeartbeat()
{
   string url = "https://telegram-relay-6x6l.onrender.com/heartbeat";
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int hourET = (dt.hour - ServerOffset + 24) % 24;
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   string body = "{\"status\":\"activo\","
                 "\"hour_et\":"  + IntegerToString(hourET)        + ","
                 "\"balance\":"  + DoubleToString(balance, 2)     + ","
                 "\"equity\":"   + DoubleToString(equity, 2)      + "}";
   char post[], result[];
   string headers = "Content-Type: application/json\r\n";
   StringToCharArray(body, post, 0, StringLen(body));
   string responseHeaders;
   int res = WebRequest("POST", url, headers, 5000, post, result, responseHeaders);
   if(res != -1)
      Print("Heartbeat enviado OK | Balance: ", DoubleToString(balance, 2));
}

int TimeDay(datetime t){MqlDateTime d; TimeToStruct(t,d); return d.day;}
//+------------------------------------------------------------------+
