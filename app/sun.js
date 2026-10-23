// Solar position after the NOAA solar calculator (accurate to ~0.01° for
// current dates). Returns azimuth (degrees clockwise from north) and apparent
// elevation (degrees, including atmospheric refraction).

const RAD = Math.PI / 180;

export function sunPosition(date, lat, lon) {
  const jd = date.getTime() / 86400000 + 2440587.5;
  const t = (jd - 2451545) / 36525;

  const l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360;
  const m = 357.52911 + t * (35999.05029 - 0.0001537 * t);
  const e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t);
  const c =
    Math.sin(m * RAD) * (1.914602 - t * (0.004817 + 0.000014 * t)) +
    Math.sin(2 * m * RAD) * (0.019993 - 0.000101 * t) +
    Math.sin(3 * m * RAD) * 0.000289;
  const trueLong = l0 + c;
  const omega = 125.04 - 1934.136 * t;
  const appLong = trueLong - 0.00569 - 0.00478 * Math.sin(omega * RAD);
  const meanObliq = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60;
  const obliq = meanObliq + 0.00256 * Math.cos(omega * RAD);
  const decl = Math.asin(Math.sin(obliq * RAD) * Math.sin(appLong * RAD));

  const y = Math.tan((obliq / 2) * RAD) ** 2;
  const eqTime =
    4 / RAD *
    (y * Math.sin(2 * l0 * RAD) -
      2 * e * Math.sin(m * RAD) +
      4 * e * y * Math.sin(m * RAD) * Math.cos(2 * l0 * RAD) -
      0.5 * y * y * Math.sin(4 * l0 * RAD) -
      1.25 * e * e * Math.sin(2 * m * RAD));

  const minutesUtc = (((date.getTime() / 60000) % 1440) + 1440) % 1440;
  const trueSolarTime = (((minutesUtc + eqTime + 4 * lon) % 1440) + 1440) % 1440;
  let hourAngle = trueSolarTime / 4 - 180;

  const latR = lat * RAD;
  const cosZen = Math.sin(latR) * Math.sin(decl) + Math.cos(latR) * Math.cos(decl) * Math.cos(hourAngle * RAD);
  const zenith = Math.acos(Math.min(1, Math.max(-1, cosZen)));
  let elev = 90 - zenith / RAD;

  let az;
  const sinZen = Math.sin(zenith);
  if (Math.abs(sinZen) < 1e-9) {
    az = 180;
  } else {
    const cosAz = (Math.sin(latR) * Math.cos(zenith) - Math.sin(decl)) / (Math.cos(latR) * sinZen);
    az = Math.acos(Math.min(1, Math.max(-1, cosAz))) / RAD;
    az = hourAngle > 0 ? (az + 180) % 360 : (540 - az) % 360;
  }

  // Refraction correction (NOAA approximation).
  let refr = 0;
  if (elev <= 85) {
    const te = Math.tan(elev * RAD);
    if (elev > 5) refr = 58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5;
    else if (elev > -0.575) refr = 1735 + elev * (-518.2 + elev * (103.4 + elev * (-12.79 + elev * 0.711)));
    else refr = -20.774 / te;
    refr /= 3600;
  }
  elev += refr;

  return { azimuth: az, elevation: elev };
}
