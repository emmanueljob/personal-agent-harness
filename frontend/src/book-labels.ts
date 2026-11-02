export function spineLabel(title:string){
 const short=title.split(':')[0].replace(/\s*\([^)]*\)/g,'').replace(/,?\s*(?:Updated and Expanded|Fully Revised.*|Second Edition.*)$/i,'').trim();
 if(short==='The Making of Prince of Persia')return 'Prince of Persia';
 if(short==='Introduction to Programmatic Advertising')return 'Programmatic Advertising';
 return short;
}
