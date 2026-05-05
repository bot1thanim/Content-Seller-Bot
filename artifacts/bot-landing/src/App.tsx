import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Flame, Shield, Zap, Lock, Eye, Key, Coins, CheckCircle2 } from "lucide-react";
import NotFound from "@/pages/not-found";

const queryClient = new QueryClient();

function Home() {
  const telegramLink = "https://t.me/your_bot";

  const packages = [
    { price: "2", videos: 1 },
    { price: "9", videos: 5, popular: true },
    { price: "16", videos: 10 },
    { price: "30", videos: 20 },
    { price: "65", videos: 50, premium: true },
    { price: "85", videos: 70 },
    { price: "110", videos: 100 },
    { price: "180", videos: 200, ultimate: true },
  ];

  return (
    <div className="min-h-[100dvh] w-full bg-background text-foreground overflow-x-hidden selection:bg-primary/30">
      
      {/* Noise overlay for texture */}
      <div className="fixed inset-0 opacity-[0.03] pointer-events-none z-50 bg-[url('https://grainy-gradients.vercel.app/noise.svg')]"></div>

      {/* Hero Section */}
      <section className="relative min-h-[90vh] flex flex-col items-center justify-center pt-20 pb-16 px-4">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-primary/20 rounded-full blur-[120px] pointer-events-none opacity-50"></div>
        <div className="relative z-10 text-center max-w-4xl mx-auto space-y-8">
          <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full border border-primary/30 bg-primary/10 text-primary text-sm font-medium mb-4 animate-in fade-in slide-in-from-bottom-4 duration-1000">
            <Lock className="w-4 h-4" />
            <span>המועדון הסגור נפתח לזמן מוגבל</span>
          </div>
          <h1 className="text-5xl md:text-7xl font-black tracking-tight leading-tight text-white animate-in fade-in slide-in-from-bottom-8 duration-1000 delay-150">
            בוט התכנים <span className="text-transparent bg-clip-text bg-gradient-to-r from-primary to-orange-500">האסורים</span>
          </h1>
          <p className="text-xl md:text-2xl text-muted-foreground max-w-2xl mx-auto animate-in fade-in slide-in-from-bottom-8 duration-1000 delay-300">
            תכנים בלעדיים, שלא תמצאו בשום מקום אחר. אנונימיות מוחלטת, גישה מיידית, וחווית צפייה ללא גבולות.
          </p>
          <div className="pt-8 flex flex-col sm:flex-row items-center justify-center gap-4 animate-in fade-in slide-in-from-bottom-8 duration-1000 delay-500">
            <Button size="lg" className="w-full sm:w-auto h-14 px-8 text-lg font-bold bg-primary hover:bg-primary/90 text-white rounded-full shadow-[0_0_40px_-10px_rgba(225,29,72,0.8)] transition-all hover:scale-105" onClick={() => window.location.href = telegramLink}>
              היכנסו לבוט עכשיו
            </Button>
            <Button size="lg" variant="outline" className="w-full sm:w-auto h-14 px-8 text-lg rounded-full border-muted-foreground/30 hover:bg-white/5" onClick={() => document.getElementById('packages')?.scrollIntoView({ behavior: 'smooth' })}>
              צפו בחבילות
            </Button>
          </div>
        </div>
      </section>

      {/* Hook Section */}
      <section className="py-24 px-4 bg-black/40 border-y border-white/5 relative">
        <div className="max-w-6xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-8 text-center">
          <div className="space-y-4 p-6 rounded-2xl bg-white/5 border border-white/10 hover:border-primary/50 transition-colors">
            <div className="w-16 h-16 mx-auto bg-primary/20 rounded-full flex items-center justify-center text-primary">
              <Eye className="w-8 h-8" />
            </div>
            <h3 className="text-xl font-bold text-white">בלעדיות מוחלטת</h3>
            <p className="text-muted-foreground">תכנים שווים שמופצים רק כאן. לא תמצאו אותם ברשת החופשית.</p>
          </div>
          <div className="space-y-4 p-6 rounded-2xl bg-white/5 border border-white/10 hover:border-primary/50 transition-colors">
            <div className="w-16 h-16 mx-auto bg-primary/20 rounded-full flex items-center justify-center text-primary">
              <Shield className="w-8 h-8" />
            </div>
            <h3 className="text-xl font-bold text-white">אנונימיות מובטחת</h3>
            <p className="text-muted-foreground">הכל מתבצע דרך טלגרם. ללא פרטים מזהים, ללא עקבות.</p>
          </div>
          <div className="space-y-4 p-6 rounded-2xl bg-white/5 border border-white/10 hover:border-primary/50 transition-colors">
            <div className="w-16 h-16 mx-auto bg-primary/20 rounded-full flex items-center justify-center text-primary">
              <Zap className="w-8 h-8" />
            </div>
            <h3 className="text-xl font-bold text-white">גישה מיידית</h3>
            <p className="text-muted-foreground">רכשתם? התוכן אצלכם בשניות. ללא המתנה מיותרת.</p>
          </div>
        </div>
      </section>

      {/* Pricing / Packages */}
      <section id="packages" className="py-24 px-4 relative">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-16 space-y-4">
            <h2 className="text-4xl md:text-5xl font-black text-white">חבילות פרימיום</h2>
            <p className="text-xl text-muted-foreground">בחרו את החבילה שמתאימה לכם. שלמו ב-PayPal או במטבעות.</p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {packages.map((pkg, i) => (
              <Card key={i} className={`relative overflow-hidden bg-white/5 border-white/10 hover:border-primary/50 transition-all duration-300 hover:-translate-y-2 ${pkg.popular || pkg.premium || pkg.ultimate ? 'border-primary/30 shadow-[0_0_30px_-15px_rgba(225,29,72,0.4)]' : ''}`}>
                {(pkg.popular || pkg.premium || pkg.ultimate) && (
                  <div className="absolute top-0 right-0 left-0 bg-gradient-to-r from-primary/80 to-orange-600/80 text-white text-xs font-bold py-1 text-center">
                    {pkg.popular && 'הנמכרת ביותר'}
                    {pkg.premium && 'חבילת פרימיום'}
                    {pkg.ultimate && 'למכורים בלבד'}
                  </div>
                )}
                <CardContent className="p-8 text-center flex flex-col h-full justify-between gap-6">
                  <div className="space-y-2 pt-4">
                    <h3 className="text-2xl font-bold text-white">{pkg.videos} סרטונים</h3>
                    <div className="flex items-center justify-center gap-1">
                      <span className="text-4xl font-black text-primary">₪{pkg.price}</span>
                    </div>
                  </div>
                  <ul className="space-y-3 text-right">
                    <li className="flex items-center gap-2 text-sm text-muted-foreground">
                      <CheckCircle2 className="w-4 h-4 text-primary" />
                      איכות מקסימלית
                    </li>
                    <li className="flex items-center gap-2 text-sm text-muted-foreground">
                      <CheckCircle2 className="w-4 h-4 text-primary" />
                      זמין לתמיד
                    </li>
                  </ul>
                  <Button className="w-full mt-4 bg-white/10 hover:bg-primary text-white" onClick={() => window.location.href = telegramLink}>
                    רכוש עכשיו
                  </Button>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* Coins System */}
      <section className="py-24 px-4 bg-gradient-to-b from-transparent to-primary/5 border-y border-white/5">
        <div className="max-w-4xl mx-auto text-center space-y-8">
          <div className="inline-flex items-center justify-center w-20 h-20 rounded-full bg-yellow-500/20 text-yellow-500 mb-4 shadow-[0_0_50px_-10px_rgba(234,179,8,0.5)]">
            <Coins className="w-10 h-10" />
          </div>
          <h2 className="text-4xl font-black text-white">לא רוצים לשלם? תרוויחו מטבעות!</h2>
          <p className="text-xl text-muted-foreground leading-relaxed">
            מערכת התגמולים שלנו מאפשרת לכם ליהנות מהתכנים לגמרי בחינם. הביאו חברים לבוט והתחילו לצבור מטבעות.
          </p>
          
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-12">
            <div className="bg-white/5 p-6 rounded-2xl border border-white/10">
              <div className="text-3xl font-black text-yellow-500 mb-2">1</div>
              <div className="text-lg text-white font-bold">הזמנה אחת</div>
              <div className="text-sm text-muted-foreground">שווה מטבע 1</div>
            </div>
            <div className="bg-white/5 p-6 rounded-2xl border border-white/10">
              <div className="text-3xl font-black text-yellow-500 mb-2">10</div>
              <div className="text-lg text-white font-bold">מטבעות</div>
              <div className="text-sm text-muted-foreground">שווים ₪1 לרכישה</div>
            </div>
            <div className="bg-white/5 p-6 rounded-2xl border border-white/10">
              <div className="text-3xl font-black text-yellow-500 mb-2">∞</div>
              <div className="text-lg text-white font-bold">ללא הגבלה</div>
              <div className="text-sm text-muted-foreground">הזמינו כמה שיותר</div>
            </div>
          </div>
          
          <div className="pt-8">
            <Button size="lg" className="h-14 px-8 text-lg font-bold bg-yellow-500 hover:bg-yellow-600 text-black rounded-full" onClick={() => window.location.href = telegramLink}>
              קבלו קישור אישי להזמנות
            </Button>
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section className="py-24 px-4">
        <div className="max-w-3xl mx-auto space-y-12">
          <div className="text-center">
            <h2 className="text-4xl font-black text-white">שאלות נפוצות</h2>
          </div>
          
          <Accordion type="single" collapsible className="w-full">
            <AccordionItem value="item-1" className="border-white/10">
              <AccordionTrigger className="text-lg font-bold text-white hover:text-primary">איך מתבצע התשלום?</AccordionTrigger>
              <AccordionContent className="text-muted-foreground text-base">
                התשלום מתבצע בצורה מאובטחת דרך PayPal ישירות בתוך ממשק הבוט. לא נשמרים פרטי אשראי אצלנו.
              </AccordionContent>
            </AccordionItem>
            <AccordionItem value="item-2" className="border-white/10">
              <AccordionTrigger className="text-lg font-bold text-white hover:text-primary">האם אפשר להוריד את הסרטונים?</AccordionTrigger>
              <AccordionContent className="text-muted-foreground text-base">
                חלק מהסרטונים ניתנים לצפייה ישירה בלבד מטעמי אבטחה, וחלק ניתנים להורדה. הכל מפורט בתיאור של כל סרטון בבוט.
              </AccordionContent>
            </AccordionItem>
            <AccordionItem value="item-3" className="border-white/10">
              <AccordionTrigger className="text-lg font-bold text-white hover:text-primary">האם האנונימיות באמת מובטחת?</AccordionTrigger>
              <AccordionContent className="text-muted-foreground text-base">
                כן. אנחנו עובדים רק עם מזהה הטלגרם שלך (ID). אין צורך בשם, טלפון או כל פרט מזהה אחר כדי להשתמש בבוט.
              </AccordionContent>
            </AccordionItem>
            <AccordionItem value="item-4" className="border-white/10">
              <AccordionTrigger className="text-lg font-bold text-white hover:text-primary">איך משתמשים במטבעות?</AccordionTrigger>
              <AccordionContent className="text-muted-foreground text-base">
                בכל פעם שתרצו לרכוש חבילה, הבוט ישאל אתכם אם תרצו להשתמש במטבעות שצברתם כדי לקבל הנחה או לקבל את החבילה בחינם.
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </div>
      </section>

      {/* Footer CTA */}
      <footer className="py-16 px-4 bg-black border-t border-white/10 text-center">
        <div className="max-w-2xl mx-auto space-y-8">
          <h2 className="text-3xl font-black text-white">מוכנים לגלות מה מסתתר בפנים?</h2>
          <Button size="lg" className="h-16 px-12 text-xl font-black bg-white text-black hover:bg-gray-200 rounded-full w-full sm:w-auto" onClick={() => window.location.href = telegramLink}>
            לכניסה לבוט
          </Button>
          <p className="text-sm text-muted-foreground">
            © 2025 בוט התכנים האסורים. כל הזכויות שמורות. השימוש בבוט מגיל 18 ומעלה בלבד.
          </p>
        </div>
      </footer>

    </div>
  );
}

function Router() {
  return (
    <Switch>
      <Route path="/" component={Home} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;