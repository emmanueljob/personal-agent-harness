// Schema and neutral defaults only. Personal content is loaded from /api/portfolio.
export type Portfolio = {
 name:string; shortName:string; surname:string; role:string; focus:string; location:string;
 linkedin:string; source:string; sourceDate:string; summary:string; education:string;
 intro:string; footer:string; resumeLabel:string; careerQuestion:string; focusLines:string[];
 suggestions:{label:string;question:string}[];
 projects:{id:string;eyebrow:string;title:string;company:string;description:string;detail:string;outcome:string;question:string}[];
 career:{company:string;role:string;dates:string;note:string}[];skills:string[];
};
export const emptyPortfolio:Portfolio={name:'Your name',shortName:'the owner',surname:'',role:'',focus:'',location:'',linkedin:'',source:'',sourceDate:'',summary:'',education:'',intro:'An AI guide to the work, the ideas, and the person behind them.',footer:'A conversation, with a human at the heart of it.',resumeLabel:'PROFILE NOT CONFIGURED',careerQuestion:'Tell me about the owner’s career using the supplied profile.',focusLines:[],suggestions:[],projects:[],career:[],skills:[]};
