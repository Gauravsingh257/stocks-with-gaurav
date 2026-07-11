"use client";

/**
 * AnalyticsScripts — loads GA4, Microsoft Clarity, and (optionally) PostHog,
 * each gated by its env var, and fires a named page-view event on every route
 * change (App Router SPA navigations don't reload the page).
 *
 * Product-validation phase instrumentation. Renders nothing visible.
 */
import Script from "next/script";
import { useEffect } from "react";
import { usePathname } from "next/navigation";
import {
  GA4_ID, CLARITY_ID, POSTHOG_KEY, POSTHOG_HOST,
  pageview, track, routeEvent, endSession,
} from "@/lib/analytics";

export default function AnalyticsScripts() {
  const pathname = usePathname();

  // Fire page_view + a named "viewed/opened" event on each navigation.
  useEffect(() => {
    if (!pathname) return;
    pageview(pathname);
    const named = routeEvent(pathname);
    if (named) track(named);
  }, [pathname]);

  // Record the session ending on tab/browser close (once). `pagehide` is the
  // reliable unload signal on both desktop and mobile Safari.
  useEffect(() => {
    let ended = false;
    const onHide = () => {
      if (ended) return;
      ended = true;
      endSession("closed_or_left");
    };
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, []);

  return (
    <>
      {/* Google Analytics 4 — send_page_view:false so SPA views aren't double-counted */}
      {GA4_ID && (
        <>
          <Script src={`https://www.googletagmanager.com/gtag/js?id=${GA4_ID}`} strategy="afterInteractive" />
          <Script id="ga4-init" strategy="afterInteractive">
            {`window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','${GA4_ID}',{send_page_view:false});`}
          </Script>
        </>
      )}

      {/* Microsoft Clarity — session replay + heatmaps */}
      {CLARITY_ID && (
        <Script id="ms-clarity" strategy="afterInteractive">
          {`(function(c,l,a,r,i,t,y){c[a]=c[a]||function(){(c[a].q=c[a].q||[]).push(arguments)};t=l.createElement(r);t.async=1;t.src="https://www.clarity.ms/tag/"+i;y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);})(window,document,"clarity","script","${CLARITY_ID}");`}
        </Script>
      )}

      {/* PostHog (optional) — product analytics / funnels */}
      {POSTHOG_KEY && (
        <Script id="posthog-init" strategy="afterInteractive">
          {`!function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.async=!0,p.src=s.api_host+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],u.toString=function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e},u.people.toString=function(){return u.toString(1)+".people (stub)"},o="capture identify alias people.set people.set_once set_config register register_once unregister opt_out_capturing has_opted_out_capturing opt_in_capturing reset isFeatureEnabled onFeatureFlags getFeatureFlag getFeatureFlagPayload reloadFeatureFlags group updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures getActiveMatchingSurveys getSurveys onSessionId".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);posthog.init('${POSTHOG_KEY}',{api_host:'${POSTHOG_HOST}'});`}
        </Script>
      )}
    </>
  );
}
