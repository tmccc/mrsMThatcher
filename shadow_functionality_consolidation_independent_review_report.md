# Shadow-functionality consolidation independent review

> **Superseded on 19 July 2026:** the operator withdrew the unattended-post
> wording gate. All 610 attribution-eligible quotations are restored to regular
> posting eligibility. The 545-quote candidate reviewed below was removed and
> must not be deployed.

Date: 19 July 2026
Project: `/disks/disk1/etc/mrsMThatcher`
Reviewed commit base: `b8c66ec6afbcb8085cea5cac2168cb6956f76f53`

## Executive verdict

The cumulative consolidation diff was reviewed directly. The hybrid-removal, generated-identity suspension, lifecycle validation, regular-post wording gate, selector transition and uncertainty-disclosure paths are technically sound after two narrow corrections found during this review.

The proposed regular-post population is **545**, not 535. Ten of the initially excluded 75 records have high-confidence variant, excerpt or normalised wording backed by a Thatcher-authored book named in `source_event` and a page or chapter in `stable_locator`. Treating only the locator field as evidence incorrectly excluded them. The policy now recognises that split primary citation without weakening any other eligibility rule.

The wording-filtered semantic-veto deployment candidate is **not ready to deploy**. Its sole claimed quotation with no globally allowed historical image is based on incomplete candidate coverage: 35 of 91 pairs exist, 25 are unknown, and 56 are absent. A source-captioned Gorbachev photograph is in the authorised image corpus but its pair with this Gorbachev-specific quotation is absent. This is a manifest-generation defect, not evidence that all 91 images are unsafe.

No live configuration or manifest was changed. The active 610-quotation semantic-veto shadow remains non-enforcing and unchanged in decision content.

## Review method

The review inspected the cumulative Git diff, the completed quotation packets, active and candidate veto manifests, image and quotation contracts, current structured logs, production selector code, reply retrieval imports and call sites, generated-image gates, digest rendering, lifecycle register, and relevant tests. Structured JSON was parsed directly; Markdown summaries were used only as secondary evidence.

The live `lines_used.json` was read once and copied to temporary test state. No test wrote to live state, receipts, histories, analytics data or configuration.

## Exact initial 75-record exclusion audit

Initial classification:

| Classification | Count |
|---|---:|
| unverified wording | 41 |
| composite wording | 9 |
| paraphrase | 6 |
| variant, excerpt or normalisation | 19 |
| **Total** | **75** |

Review outcome:

| Outcome | Count |
|---|---:|
| restored from the 19 variants/excerpts/normalisations | 10 |
| variants/excerpts/normalisations still excluded | 9 |
| other records still excluded | 56 |
| **Final wording exclusions** | **65** |
| **Final unattended regular-post eligibility** | **545** |

The full record-level audit follows. “Primary evidence” reproduces the relevant structured packet fields; it is not a new attribution claim.

| Quote ID | Exact source text | Class / confidence | Primary evidence in packet | Initial exclusion reason | Review outcome |
|---|---|---|---|---|---|
| 0056972ab9debcb840c36ac23ad0387e715cb22fc4ed49dbcf35159f83592aa5 | If someone is confronting our essential liberties, if someone is inflicting injuries and harm, by God I'll confront them! | variant / high | no public URL; spiegel.de; unknown | verified_variant_lacks_reliable_primary_evidence | EXCLUDED: evidence does not satisfy policy |
| 0195075998545ab93834a331f35b4ac0d8c76543495757aa72874ad8e9fb3448 | Inflation is the parent of unemployment and the unseen robber of those who have saved. | composite / high | Margaret Thatcher Foundation; Margaret Thatcher Foundation Archive, Speech to Conservative Party Conference, October 10, 1980; Margaret Thatcher Foundation Archive, Speech to Conservative Party Conference, October 10, 1980 | wording_status_composite_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 063b2c3fd5a61ea4fd955bed8d11b523ee5634086c647e378aaf33e3784abcf7 | Conservatives have excellent credentials to speak about human rights. By our efforts, and with precious little help from self-styled liberals, we were largely responsible for securing liberty for a substantial share of the world's population and defending it for most of the rest. | unverified / high | no public URL; Attribution record: keepinspiring.me; Unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 073d64fa2701a07285b64d02867d75772935b2682ae78dd45124a2a2b55ddc08 | It seems like cloud cuckoo land... If anyone is suggesting that I would go to Parliament and suggest the abolition of the pound sterling - no! We have made it quite clear that we will not have a single currency imposed on us. | composite / high | Hansard; Hansard, House of Commons, 30 October 1990, vol 178 cc869-83; Hansard, House of Commons, 30 October 1990, vol 178 cc869-83 | wording_status_composite_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 0acb6cd5c5ec0ed58b882f3f4077b6f65f04b100ebfde6ace075c1993736b51a | This Party of ours has been on the defensive for too long. The time has come to counter-attack. The intellectual counter-attack is as important as the counter-attack in Parliament and in the constituencies. If we can win the battle of ideas, then the war will already be half-won. | composite / high | Margaret Thatcher Foundation; Margaret Thatcher Foundation Document 102663; Margaret Thatcher Foundation Document 102663 | wording_status_composite_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 0e54df9b0adb2337f436d7346964965c56669c997386d75f2d58104ec88e1766 | I don't believe they [the voters] want a Government to be so flexible it becomes invertebrate. You don't want a Government full of flexi-toys. | composite / high | Margaret Thatcher Foundation; Margaret Thatcher Foundation Archive; Margaret Thatcher Foundation Archive | wording_status_composite_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 1005a705248e9a619623b60321cda394a7507124823e6ac6926857339fad48fc | And really, you know, politicians, I think should sometimes just be a little bit more modest about their abilities than they are. We can't run everything - and we shouldn't try. | unverified / low | no public URL; Attribution record: devizine.com; unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 1085ff6a77a0bf02ea5c2621cc8d546e47637f2e363979687c62ba73f16504fa | Few people really believe that a country - any more than an individual - can go on indefinitely living on borrowed money; or that a community can long continue to pay itself for producing less. | unverified / low | no public URL; Attribution record: parliament.uk; unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 1948eccad1dcd6bff2e25fef1bf600b5912093154fd62284035e82da05e65c55 | What makes good neighbours is often good fences. | excerpt / high | reliable Thatcher-authored publication locator; Publication of the book 'Statecraft: Strategies for a Changing World'; Introduction, p. xxv; Introduction, p. xxv | verified_variant_lacks_reliable_primary_evidence | RESTORED: split Thatcher-authored work and page/chapter citation satisfies policy |
| 2001778d5e133550fd8a457a2ef7a1834f5493f1817415027c96add1ca841365 | Socialism and political correctness has resurfaced in the language and programmes of "group rights". The process has gone furthest in the United States: though I suspect that if Britain were so foolish as to elect a Labour Government we could quickly catch up. | composite / high | Margaret Thatcher Foundation; Margaret Thatcher Foundation Archive; Margaret Thatcher Foundation Archive | wording_status_composite_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 25b16b1294599728b25379c35a6c142b676e02bb6392c307060c9d8567c05cb9 | Christmas is a day of meaning and traditions, a special day spent in the warm circle of family and friends. | unverified / high | no public URL; Attribution record: goodreads.com; unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 272c9af2e31064654a8ab74480de7d7357795514e12d947587eb155f31cca996 | Free enterprise has enabled the creative and the acquisitive urges of man to be given expression in a way which benefits all members of society. Let free enterprise fight back now, not for itself, but for all those who believe in freedom. | unverified / medium | official Conservative publication; Attribution record: conservatives.com; Unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 2b351e489fc2b20f68c7f7867b3b17fb1636bb3ed2e7b6593e3345e10b09d5a2 | Those of us who believed in the sanctity of the human spirit had no doubt that the communist system would eventually collapse, but it is remarkable that so many people, mostly intellectuals, were taken in by communism's lies. | unverified / high | no public URL; Attribution record: brainly.com | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 2b6aa4dc33fc5e8ff1c9e1a4e90464d18bbd18c323bc525dd05f99b1a77d6594 | We must recognise certain groups of people who need help, but the rest of us must take responsibility for ourselves, and we must stop being such a subsidised-minded society. | unverified / high | Margaret Thatcher Foundation; Margaret Thatcher Foundation, Document 106689; Margaret Thatcher Foundation, Document 106689 | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 2f5df9185bdf06e8ffe184a0e65edfe238a7dcea2a3b482103f763947aa05467 | I owe nothing to Women's Lib. | paraphrase / high | Margaret Thatcher Foundation; Margaret Thatcher Foundation Archive; Margaret Thatcher Foundation Archive | wording_status_paraphrase_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 313172d18e2d915e514e4a202a8b1bcbb077472c2504dee63fe98edaf60e0b3a | It is well known that the advocates of European federalism have never lacked access to funding. Not so those who seek to preserve British sovereignty. | unverified / high | other authoritative source; https://www.google.com/search?q=time+in+Lancashire,+GB; Unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 3ba6cfba4bbf0673ce1cf563fb9de4821aded8422c64cbcd4218bf8da951f24d | In today's world, monopoly trade unions have immense potential for destruction and can inflict severe damage and hardship on their fellow workers - indeed on their fellow trade unionists. | unverified / low | Thatcher-authored publication; Attribution record: versobooks.com; Unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 3ec9ed7c0f35b414ee6570182d1e65874cc7f5c659cdb31ecdbf8fe3ee492319 | The Conservative Party understands the individual, because it believes in him, and in his potential provided he is allowed to be his own man and not a creature of the State. | unverified / low | Margaret Thatcher Foundation; Attribution record: margaretthatcher.org; unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 3fac1e07a8d368ebe49544c6b1e580b549da46a05d7a0649421bae0af7377b47 | We believe a Government's task is to give people the opportunity, not a handout. | unverified / high | no public URL; Attribution record: demdaco.com; None | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 4c135c053d61ff9441b6aed5b77213967f43489263ee9f80b49a4155ef93737e | Nobody, not even the most hardened Labour apologist, can wish to see a repetition of the cruelty and callousness of this winter. None of us can be content with a society where such things happen without check or penalty. In that sense I am a reformer and I am offering a change. | unverified / low | no public URL; Attribution record: mdpi.com; Unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 4c9b51eb69875a896e83f9e4a07655ad8c1de12056089090f235152d46f67b61 | What is more heartless than the all-powerful State? Do not the industrious and farsighted benefit the community as well as themselves? | paraphrase / high | Margaret Thatcher Foundation; Margaret Thatcher Foundation Archive; Margaret Thatcher Foundation Archive | wording_status_paraphrase_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 4e08964fca7fabd939c7be1842bcc25133d0a5d8eccaaf78d4c36689350daa9b | Countries trade with each other because that is the way to advance their interests. We do not need to beg people to trade with us - as long as we have something that people want, of a quality they expect and at a price they are prepared to pay. | excerpt / medium | Thatcher-authored publication; Iain Dale (1997), 'As I said to Denis--: the Margaret Thatcher book of quotations'; Iain Dale (1997), 'As I said to Denis--: the Margaret Thatcher book of quotations' | verified_variant_lacks_reliable_primary_evidence | EXCLUDED: evidence does not satisfy policy |
| 4e92014dc14fd9cce562099b03ccfdf480fd5909414483edf4a3e28dc6e85c80 | The Labour Party has now been taken over by extremists. The Labour Party is now committed to a programme which is frankly and unashamedly Marxist, a programme initiated by its National Executive and now firmly endorsed by its official Party Conference | composite / high | Margaret Thatcher Foundation; Margaret Thatcher Foundation Archive; Margaret Thatcher Foundation Archive | wording_status_composite_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 4f5e783f4957dc615742df2b827214e539a5123af1b4863822ba2e52684a0d80 | Unlike some of my colleagues, I never ceased to believe that the level of unemployment was related to the extent of trade union power. The unions had priced many of their members out of jobs by demanding excessive wages for insufficient output, so making goods uncompetitive. | variant / high | reliable Thatcher-authored publication locator; The Downing Street Years (Memoir); Page 272; Page 272 | verified_variant_lacks_reliable_primary_evidence | RESTORED: split Thatcher-authored work and page/chapter citation satisfies policy |
| 5189f2683ec11a28cc0507226cff34c24e7341a489395bf5e472cc437179499d | If... many influential people have failed to understand, or have just forgotten, what we were up against in the Cold War and how we overcame it, they are not going to be capable of securing, let alone enlarging, the gains that liberty has made. | unverified / low | no public URL; Attribution record: weirs.com; Unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 52f9b9f99f66ff3bc786183803f3a8d68277604471cd411027441989337c9351 | There are still people in my party who believe in consensus politics. I regard them as Quislings, as traitors... I mean it. | variant / high | canonical locator only; Hugo Young, 'One of Us' (1989), p. 193; Hugo Young, 'One of Us' (1989), p. 193 | verified_variant_lacks_reliable_primary_evidence | EXCLUDED: evidence does not satisfy policy |
| 53699726287c4fed9ef2b086e53cc2938b1fd482a7b5541f4718df3439598215 | What is success? I think it is a mixture of having a flair for the thing that you are doing; knowing that it is not enough, that you have got to have hard work and a certain sense of purpose. | unverified / low | no public URL; Attribution record: indiatimes.com; Unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 5440b194ff60f3b728644d36cb483d0c23448616b4c79588e798177e1ffa0483 | Europe is not based on a common language, culture and values... Europe is a result of plans. It is, in fact, a classic utopian project, a monument to the vanity of intellectuals, a programme whose inevitable destiny is failure; only the scale of the final damage done is in doubt. | variant / high | reliable Thatcher-authored publication locator; Statecraft: Strategies for a Changing World (Book); Page 359; Page 359 | verified_variant_lacks_reliable_primary_evidence | RESTORED: split Thatcher-authored work and page/chapter citation satisfies policy |
| 56c77eedf86a10a6748dc8e20d096e415708541735c07f5c88a338f49d29cc9c | Look at a day when you are supremely satisfied at the end. It's not a day when you lounge around doing nothing; it's when you've had everything to do, and you've done it. | unverified / medium | Thatcher-authored publication; Iain Dale (ed.), 'As I Said to Denis: The Margaret Thatcher Book of Quotations' (1997); Iain Dale (ed.), 'As I Said to Denis: The Margaret Thatcher Book of Quotations' (1997) | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 573412501ec88441938dae368acf42712f3952fd31aeab5c85dcd10ec7c968a4 | Standing in the middle of the road is very dangerous; you get knocked down by the traffic from both sides. | variant / high | canonical locator only; Jim Prior, 'A Balance of Power' (1986), p. 106; Jim Prior, 'A Balance of Power' (1986), p. 106 | verified_variant_lacks_reliable_primary_evidence | EXCLUDED: evidence does not satisfy policy |
| 5d1b1cb2460a02dd8fb0b61823926b330716189913eeea2c75c03d5d0ada5cf2 | When hecklers stand up ... I get a mental jump for joy. It gives me something to get my teeth into - and the audiences love it. | unverified / low | Thatcher-authored publication; Iain Dale, 'As I Said to Denis: The Margaret Thatcher Book of Quotations' (1997); Iain Dale, 'As I Said to Denis: The Margaret Thatcher Book of Quotations' (1997) | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 5ec95cef5d0ad0f79c87c05b6a9e74eb10c0858b0f9cf737e34fd13ca381c42a | I have enormous admiration for the Jewish people, inside or outside Israel. There have always been Jewish members of my staff and indeed my Cabinet. In fact I just wanted a Cabinet of clever, energetic people - and frequently that turned out to be the same thing. | normalised / high | reliable Thatcher-authored publication locator; The Downing Street Years (Memoir); Page 509; Page 509 | verified_variant_lacks_reliable_primary_evidence | RESTORED: split Thatcher-authored work and page/chapter citation satisfies policy |
| 5f14e6e600773cf394a3f3a3ae21aef10f108691eef50093002571765a5a4a82 | We cannot conceivably accept that a country can simply march into a neighbour, which is an independent country and a full member of the UN, and annex it. | unverified / low | other authoritative source; https://www.google.com/search?q=time+in+Kuwait; Unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 5f343702a183a99143ff0935165238b5d5ee0732521a258470450e207fb147e5 | It is our job to go about telling everybody to obey the law. | paraphrase / high | Margaret Thatcher Foundation; Margaret Thatcher Foundation Archive; Margaret Thatcher Foundation Archive | wording_status_paraphrase_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 677bda2ba3097d2452133f66a0eab9c9740a06a0be8d53bdd712f52b53ff7bab | The Russians put guns before butter. We put just about everything before guns. | variant / high | other authoritative source; https://www.google.com/search?q=time+in+Russia; Paragraph discussing the Soviet Politburo and military spending. | verified_variant_lacks_reliable_primary_evidence | EXCLUDED: evidence does not satisfy policy |
| 685ddfab242fe45cafc203a937769a4fe925423baf80e022b6e2e4411dd3ce90 | When socialist countries are to be found helping out capitalist countries in their hour of need rather than vice-versa, then - and only then - should we question the system which makes us rich, healthy and secure. | unverified / high | no public URL; Attribution record: rcpbml.org.uk; Unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 6abe034f1de82a057490eb8b4b3e8a8900c7064a80bddcc7be2a742404f078e2 | People's greatest fear is that they will lose their national identity... we ought not to be moving towards government by a technocratic elite: that is precisely what the peoples of Eastern Europe are moving away from. | unverified / high | canonical locator only; No stable locator available; quote is unverified.; No stable locator available; quote is unverified. | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 738f5f6c9aa58ebc32cdae4adbbf725cdb1fd2178d4ac3f8af725776c67a04c9 | The application of collective guilt, running from one generation to another, is a dangerous doctrine which would leave few modern nations unscathed. | unverified / high | no public URL; Attribution record: patimes.org; unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 74539c04c492f1b8147b584a781b2f32c7f4f68e8c2f6773ff9415ff31c1a2b3 | The only way to do the best you can is to work as hard as you can. | unverified / medium | no public URL; Attribution record: nav.al; N/A | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 760a25127dfb9c39cd24af73cf86cbbb76b89d31f6ecdc1eba90b09b0923bbf2 | The doomsters' favourite subject today is climate change. Clearly no plan to alter climate could be considered on anything but a global scale, it provides a marvellous excuse for worldwide, supra-national socialism. | composite / high | canonical locator only; Statecraft (2002), Chapter 10 or 11 (often cited around page 449-451); Statecraft (2002), Chapter 10 or 11 (often cited around page 449-451) | wording_status_composite_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 78fac4018710af853f7eac01666370afad7551c24b604d19df3b5a710f7c5682 | I tell you what they really mean, they mean, 'We don't like the expenditure we have agreed, we are unwilling to raise the tax to pay for it. Let us print the money instead.' The most immoral path of all. | excerpt / medium | canonical locator only; Forbes article by Kyle Smith, April 10, 2013; Forbes article by Kyle Smith, April 10, 2013 | verified_variant_lacks_reliable_primary_evidence | EXCLUDED: evidence does not satisfy policy |
| 7c29b290a8e1898c86c39b0cdd23c60161ca73cc068d4a888a19f1f7f3967d0d | I believe in the acceptance of personal responsibility, freedom of choice, and the British Empire, which took freedom and the rule of law to countries which would never have known it otherwise. | unverified / medium | other authoritative source; https://www.google.com/search?q=time+in+Perth,+AU; N/A | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 7fc6d2da4b0307b6adbcd9b44aa0b9714655d9a0879ac76158caa26e12381bc2 | It is always important in matters of high politics to know what you do not know. Those who think that they know, but are mistaken, and act upon their mistakes, are the most dangerous people to have in charge. | variant / high | reliable Thatcher-authored publication locator; Publication of 'Statecraft: Strategies for a Changing World'; Page 104; Page 104 | verified_variant_lacks_reliable_primary_evidence | RESTORED: split Thatcher-authored work and page/chapter citation satisfies policy |
| 8143e19d5c4d4e159aa40941118a0aeadf1ea316ed4b0f4ba9f93345326fc407 | I didn't realise how absolutely useless the House of Lords are. There they are, they just go along to collect 15,000 a year. They've got no guts. They should be defending Britain against the transfer of power to Maastricht and our loss of sovereignty and our loss of identity. | variant / high | canonical locator only; The Journals of Woodrow Wyatt, Volume Three, ed. Sarah Curtis (2000); The Journals of Woodrow Wyatt, Volume Three, ed. Sarah Curtis (2000) | verified_variant_lacks_reliable_primary_evidence | EXCLUDED: evidence does not satisfy policy |
| 876bdb8ea63e16f1f17fa2e85a46a2bdee820cc80713b98bda78fa49c2a4d6eb | I believe our way of life is infinitely superior for every human being than any which the Communist creed can offer. | unverified / medium | canonical locator only; Not found in primary archives; Not found in primary archives | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 8ad833d18c206e9f31e4e45e8479c99ccefd3ecf2c9cc6d5b7e2ef92c3242dc9 | I just owe almost everything to my father and it's passionately interesting for me that the things that I learned in a small town, in a very modest home, are just the things that I believe have won the election. | composite / high | Margaret Thatcher Foundation; Margaret Thatcher Foundation archive, 'Remarks on becoming Prime Minister (St Francis's prayer)'; Margaret Thatcher Foundation archive, 'Remarks on becoming Prime Minister (St Francis's prayer)' | wording_status_composite_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 8c839e92d3961147ef0070f049a2caa7f0c070bffafe4988153659825d9fa50b | Oh, I have lots of human weaknesses, who hasn't? | unverified / low | no public URL; Attribution record: sunchina.co.uk; N/A | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 8f7f5440bd005d8059bdc7d715d19e9313792812d7602be8b4c193f38d8ac848 | The Labour Party hasn't moved forward since Karl Marx's ideas, that's why it's totally morally and spiritually bankrupt. | unverified / high | Margaret Thatcher Foundation; Attribution record: margaretthatcher.org; unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 90a6e7991136e4ca46e618b5ff3cc570de1d16f1f47f69bfdc11e7004b44ee1d | To accuse me of being inflexible is absolute poppycock. | unverified / medium | canonical locator only; Not available in primary sources; Not available in primary sources | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| 92bc2b135f748c17b8df4653ec385e5f072ca5b99b4cee0988933eccbd13839f | Everything a politician promises at election time has to be paid for either by higher taxation or by borrowing. | unverified / medium | Thatcher-authored publication; As I said to Denis--: the Margaret Thatcher book of quotations (1997); As I said to Denis--: the Margaret Thatcher book of quotations (1997) | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| a19e13783f2b7b47348d1e95ece8445a69b5bb21b8312628c2cea66ccc2563ac | Don't follow the crowd, let the crowd follow you. | unverified / high | canonical locator only; No official record exists; No official record exists | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| a7510dd49389e910c983d6d18e1b57a96b02b8c1d02e5e660aa80f1021226224 | The problem with socialism is that you eventually run out of other people's money. | paraphrase / high | Margaret Thatcher Foundation; Margaret Thatcher Foundation Archive, Document 102953; Margaret Thatcher Foundation Archive, Document 102953 | wording_status_paraphrase_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| a8afc3ebabfea07d2de701cc086c1119649217548f8e662cb09d062ea0bb5d54 | People think that at the top there isn't much room. They tend to think of it as an Everest. My message is that there is tons of room at the top. | unverified / medium | no public URL; Attribution record: clintrusch.com; N/A | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| b3645f6e9b300b993be393db1d791147d2eafad5e333e9f00d336cfb4ff89696 | I don't want a Cabinet of yes-men or yes-women. It's not healthy. I can't stand sycophants. | normalised / medium | contemporary interview; The Times (London), 1990 retrospective quoting 1977; The Times (London), 1990 retrospective quoting 1977 | verified_variant_lacks_reliable_primary_evidence | EXCLUDED: evidence does not satisfy policy |
| bf7b29c68ebee5c40813db3cb6970b29e4c6cb2ef06f80cea782cf2a6c07362f | Conservatives everywhere must go on the counter-offensive against the New Left human rights brigade, and with as much intellectual vigour as we employed in struggles with the Old Left in days gone by. | unverified / high | no public URL; Attribution record: grokipedia.com; None | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| cd6a642bb6539c6b79098f1d599f9dba9c12fc5d9a190caf74cd452f7f9d10d5 | Fear is not the basis for foreign policy. | unverified / low | no public URL; Attribution record: scirp.org; Unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| cdf29235583d7f6ff95df8d6271362b37946413f521bf6b45ba739e4001f409e | Independence and autonomy is the very air that entrepreneurship breathes and lives on. Socialist politicians plan to destroy it. | unverified / high | no public URL; Attribution record: helloswanky.com; None | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| cee74829f87281863f2754e13f81b94ca9b1987d17097535d2820453afb8f6e6 | Two things are guaranteed to destroy prosperity: socialism and inflation. | paraphrase / high | Margaret Thatcher Foundation; Margaret Thatcher Foundation Archive; Margaret Thatcher Foundation Archive | wording_status_paraphrase_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| cfe7448552ce1f14db2382ac4a56e86220db74742a887e06242f8b8acb48c2f7 | You may have to fight a battle more than once to win it. | unverified / high | canonical locator only; The Concise Columbia Dictionary of Quotations (1989), ed. Robert Andrews, p. 320; The Concise Columbia Dictionary of Quotations (1989), ed. Robert Andrews, p. 320 | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| d42784e808ec3ab021052a96662182a47acd53ba97b412e0657858fdd1e848f9 | Pennies do not come from heaven. They have to be earned here on earth. | unverified / medium | canonical locator only; ITV News Meridian and The Independent 2013 retrospectives; ITV News Meridian and The Independent 2013 retrospectives | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| d86c2c2eed058a7d24742cbd0d1e3c9284da25d7e99b6f58097bdaa8c4c49ad0 | The main contribution one can make as a student to one's country in peace or wartime is to study hard and effectively. | excerpt / high | reliable Thatcher-authored publication locator; The Path to Power (Autobiography); Chapter 2; Chapter 2 | verified_variant_lacks_reliable_primary_evidence | RESTORED: split Thatcher-authored work and page/chapter citation satisfies policy |
| d8af9aa2831786794c4dbc7cfa5b406f3d1206dfab27e8af5e867ae014ac64d3 | Socialism and Britain go ill together. It is not the British character. | unverified / medium | Thatcher-authored publication; The Margaret Thatcher Book of Quotations; The Margaret Thatcher Book of Quotations | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| d9acc52446c0e8c7b1ba16c04ffd21a9cf2329f8b64078f531aa132547445dba | I've seen and heard so many things on the BBC that infuriate me almost every day of the week - tendentious reporting, unfair comment, unbearable violence and vulgarity - that I hesitate to say yes when any part of the BBC asks me to do anything. | unverified / low | Thatcher-authored publication; The Margaret Thatcher Book of Quotations; The Margaret Thatcher Book of Quotations | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| de520cbe8c9d2b854073a3eb6c0dea4c5259da702dfbbf90e61e20516823d8ac | Europe was created by history. America was created by philosophy. | paraphrase / high | Margaret Thatcher Foundation; Margaret Thatcher Foundation Archive, Document THCR [speaking text]; Margaret Thatcher Foundation Archive, Document THCR [speaking text] | wording_status_paraphrase_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| e0770fc3d6619db33fb692301a29fbf15f0e8e2306bd568064e557a108f69ec3 | It pays to know the enemy not least because at some time you may have the opportunity to turn him into a friend. | excerpt / high | reliable Thatcher-authored publication locator; The Downing Street Years; Pages 450-453; Pages 450-453 | verified_variant_lacks_reliable_primary_evidence | RESTORED: split Thatcher-authored work and page/chapter citation satisfies policy |
| e61f14550883eef5e138633ec95c5dcfe63a5834732fa31807ea33a7e936a2f2 | We should learn the lesson that as long as a free political system, a free society and a free economy are maintained, the ingenuity of mankind is boundless. | unverified / low | no public URL; Attribution record: youtube.com; Unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| e7f47c3d78e910d0639668eca12491cb6406ad22191d4acc33dfb45562a5f44b | The feminists hate me, don't they? And I don't blame them. For I hate feminism. It is poison. | unverified / medium | canonical locator only; Paul Johnson, 'Failure of the feminists', The Spectator, March 12, 2011; Paul Johnson, 'Failure of the feminists', The Spectator, March 12, 2011 | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| e87e2815cb5136fe8d5b0ae9db6114478ea34ae7c7acd30090eb96f2b7aca918 | Our freedoms depended on our having independence, independence in the wage packet, and independence of the Government. If you rely always on a Government for your wage packet, then the source of your independence to fight that Government has gone. | composite / high | Margaret Thatcher Foundation; Margaret Thatcher Foundation Archive; Margaret Thatcher Foundation Archive | wording_status_composite_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| eb1d2ebaac7e321e67174d2db5761d2bd008341ebb04b1abac4d4a927cd7a7d4 | If my critics saw me walking over the Thames they would say it was because I couldn't swim. | unverified / high | no public URL; Attribution record: nps.gov; N/A | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| ec0799ceb8ea89c6b4472e2aba48ea71de7bf44fcb1fae6edfba90c42e3a7369 | Each demand for security, whether of employment, income or social position, implies the exclusion from such benefits of those outside the privileged group - and would generate demands for countervailing privileges from excluded groups. In such a situation all will lose. | variant / high | reliable Thatcher-authored publication locator; Publication of 'The Path to Power'; Page 51; Page 51 | verified_variant_lacks_reliable_primary_evidence | RESTORED: split Thatcher-authored work and page/chapter citation satisfies policy |
| eca9b542f2b3b84e3d917eb4dda8b4539505b74db73cfc3b5c0278bdf96593e4 | One of the things being in politics has taught me is that men are not a reasoned or reasonable sex. | unverified / low | no public URL; Attribution record: archive.org; Unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |
| f4323817daee5cef16fa5d83879823f2da5506152fcb7b1b1ce5c777ac036d4d | European history shows that, first, there's nothing necessarily benevolent about programmes of European integration; second, desire to achieve utopian plans often poses a grave threat to freedom; and third, European unity has been tried before - the outcome was far from happy. | variant / high | reliable Thatcher-authored publication locator; Publication of Statecraft: Strategies for a Changing World; Page 327; Page 327 | verified_variant_lacks_reliable_primary_evidence | RESTORED: split Thatcher-authored work and page/chapter citation satisfies policy |
| f70a07b2372de9810d6de81c187d2cca606b1d093252e96d7527d3775c814bba | I'm not hard, I'm frightfully soft - but I will not be hounded. I will not be driven anywhere against my will. | variant / high | canonical locator only; Daily Mail, 1972; cited in The Macmillan Dictionary of Quotations; Daily Mail, 1972; cited in The Macmillan Dictionary of Quotations | verified_variant_lacks_reliable_primary_evidence | EXCLUDED: evidence does not satisfy policy |
| f7e7033fc25ca1944294573e53a4eccc89a60eccb85bba232ba533b04f433a74 | Left-wing zealots have often been prepared to ride roughshod over due process and basic considerations of fairness when they think they can get away with it. For them the ends always seems to justify the means. That is precisely how their predecessors came to create the gulag. | variant / high | reliable Thatcher-authored publication locator; Publication of the book 'Statecraft: Strategies for a Changing World'; Page 273; Page 273 | verified_variant_lacks_reliable_primary_evidence | RESTORED: split Thatcher-authored work and page/chapter citation satisfies policy |
| f900baf560999c350070e4600757cfa22f640c82e510e2f9f5e2c35059263155 | Platitudes? Yes, there are platitudes. Platitudes are there because they are true. | unverified / low | no public URL; Attribution record: medium.com; Unknown | wording_status_unverified_not_unattended_post_eligible | EXCLUDED: evidence does not satisfy policy |

## Eligibility corrections

The ten restored IDs are:

- `1948eccad1dcd6bff2e25fef1bf600b5912093154fd62284035e82da05e65c55`
- `4f5e783f4957dc615742df2b827214e539a5123af1b4863822ba2e52684a0d80`
- `5440b194ff60f3b728644d36cb483d0c23448616b4c79588e798177e1ffa0483`
- `5ec95cef5d0ad0f79c87c05b6a9e74eb10c0858b0f9cf737e34fd13ca381c42a`
- `7fc6d2da4b0307b6adbcd9b44aa0b9714655d9a0879ac76158caa26e12381bc2`
- `d86c2c2eed058a7d24742cbd0d1e3c9284da25d7e99b6f58097bdaa8c4c49ad0`
- `e0770fc3d6619db33fb692301a29fbf15f0e8e2306bd568064e557a108f69ec3`
- `ec0799ceb8ea89c6b4472e2aba48ea71de7bf44fcb1fae6edfba90c42e3a7369`
- `f4323817daee5cef16fa5d83879823f2da5506152fcb7b1b1ce5c777ac036d4d`
- `f7e7033fc25ca1944294573e53a4eccc89a60eccb85bba232ba533b04f433a74`

The implementation now recognises only the evidence-backed pattern “Thatcher-authored work in `source_event` plus page/chapter locator”. Secondary recollections, quotation dictionaries, unattributed sites, vague locators, composites, paraphrases and low/medium-confidence excerpts remain excluded.

Final exclusion composition is 41 unverified, 9 composite, 6 paraphrase and 9 variants/excerpts/normalisations without adequate primary evidence.

## Selector transition: 610 to 545

The transition requested as 610 to 535 was corrected by the evidence audit to **610 to 545**. The selector stores canonical quote hashes, not source line numbers or list indices.

At the read-only snapshot used for the test, live `lines_used.json` had SHA-256 `8ff72f88c6f5eecb33e292a5e431dd451042fa30efdaccf5da6fa7314a766a7a` and 412 entries:

- 358 remained in the 545-record eligible set;
- 39 were among records becoming wording-ineligible at that point in the audit;
- 15 were older or otherwise non-current hashes.

Those old IDs are harmless because candidate construction intersects current canonical hashes with current eligibility. They do not count as eligible candidates and cannot cause index shifting.

A regression test used copied state and temporary receipt sentinels:

| Scenario | Result |
|---|---|
| partially completed old cycle | old ineligible hashes ignored; unused eligible set preserved; no reset |
| almost exhausted old cycle | the one remaining eligible quote was returned; no false exhaustion or reset |
| fully exhausted old 610-record cycle | normal cycle reset occurred once; all 545 current eligible quotes became selectable |
| receipt files | all temporary sentinel bytes unchanged |
| live `lines_used.json` | never opened for writing |

The test was also run after the offline editorial backtest importer to reproduce full-suite import order. It passed after explicitly binding its read-only corpus fixtures, eliminating test-order contamination.

## The quotation with no allowed historical-image pair

Quote ID:

`67eacce6d9e102d4cf8a316451f9b8b9c095fdc6d0cffffb5a2d445e43b3d44d`

Text:

> Socialism is nationalisation of the total means of production, distribution and exchange and the planning of that by the central government - and that is what Gorbachev is saying has not worked.

The v3 quotation contract requires `Mikhail Gorbachev` visually and does not allow a neutral portrait. That restriction is defensible for this person-specific wording. The candidate evidence is not complete:

| Pair state | Count |
|---|---:|
| veto | 10 |
| unknown because pair absent from prior manifest | 25 |
| no candidate-manifest record at all | 56 |
| allow | 0 |
| **Authorised image corpus** | **91** |

Of the ten vetoes, eight reused prior decisions because the required participant identity was not source-grounded and two are affirmative `wrong_named_entity` contradictions. The 25 unknown rows are explicitly `pair_absent_from_manifest`; they are not unsafe findings.

More importantly, image `discovered:62fe0c8d105bf2ad7c95`, SHA-256 `f271019f2226396d8fdbc5297b968240d9654591b94f65778d7a84d2bc16a63a`, has high-confidence source-caption metadata naming Margaret Thatcher, Mikhail Gorbachev and Raisa Gorbacheva. Its pair with this quotation is absent.

**Root cause:** the v3 candidate builder unions production/simulator-observed pairs and selected research candidates rather than producing all 91 pairs for this quote. The filtered manifest then interprets “no known allow” as “no globally allowed candidate”. That is a manifest-generation coverage defect. It is not bad image metadata, and it does not establish a valid global no-safe-image decision.

No pair was automatically approved or rejudged during this review. Semantic-veto enforcement remains impossible and disabled. The candidate manifest should be repaired or its global flag renamed to express known-pair coverage before deployment.

## Hybrid retrieval removal

Repository and call-graph checks covered imports, initialisation, workers, queues, embedding calls, event emission and shutdown:

- `mrsMThatcher2.py` no longer defines live hybrid configuration.
- Runtime configuration validation no longer accepts a hybrid block.
- An ignored legacy local block is stripped with a startup warning; it cannot reactivate the feature.
- `ask_grok_for_reply()` calls the unchanged lexical `retrieve_completed_evidence()` path only.
- There is no production import of `semantic_alignment.hybrid_reply_retrieval`.
- There is no production submission function, worker thread, queue, embedding initialisation, hybrid telemetry call or hybrid shutdown path.
- Source search and tests cover string-based references and ensure no embedding/model function is reached through the real reply route.
- Reply grounding, safety validation, caps and retrieval packet limits are unchanged.

Evidence retained in rotated structured logs contained 44 directly recoverable completed events: 35 different evidence sets, 5 lexical-only evidence cases, 3 same-evidence cases and 1 same-set/different-order case. There were no hybrid-only evidence cases and no failed events. The longer-lived retained summary records 50 completed comparisons and zero hybrid-only evidence. This supports moving recurring work out of production.

The offline benchmark remains available. Two fresh runs against copied output directories produced identical normalised result hashes:

`7087ee0489051686d20db15a639bc0b796615c1fe405a46a38e2ebd04bcea219`

Current benchmark metrics:

| Retriever | Recall@1 | Recall@5 | MRR |
|---|---:|---:|---:|
| lexical | 0.850598 | 0.966135 | 0.900504 |
| hybrid | 0.798805 | 0.928287 | 0.853121 |

Hard-negative accuracy was 1.0. The benchmark used the pinned local E5 revision, made no network call and wrote only to `/tmp`.

## Other shadow features

### Generated-image identity policy

The production pool remains disabled. Both production identity scoring and shadow identity scoring now require `ENABLE_GENERATED_IMAGE_POOL`; startup audit loading, candidate scoring and telemetry are skipped when the pool is disabled. Tests prove that enabling the pool later preserves existing scoring behaviour.

Generated-image analyses, manifests, audit data, review tooling, simulators and identity-policy scoring functions remain available. No generated-image configuration was activated.

### Semantic veto

The currently configured 610-quote v3 shadow remains active, fail-open, observational and non-enforcing. Its pair decisions were not changed:

- quotes: 610
- images: 91
- pairs: 22,066
- allow: 21,938
- veto: 128
- SHA-256: `7016458cf516487f987188c73331b7d9af2f3e2518988c3b995785745cfd0170`

Code now selects the expected runtime quote-ID set by manifest policy version so a future wording-filtered manifest can be validated, but the active manifest follows its existing 610-quote path. Selection, scores, RNG, tie-breaking and posting are unaffected.

### Original-editorial shadow

The original-editorial scorer remains active only as an observational shadow. Its scoring, candidate ordering and log path are unchanged. Tests confirm it remains non-enforcing and produces no production-state mutation.

## Lifecycle register

`shadow_feature_lifecycle.json` is schema-validated and fail-closed. Current states are:

| Feature | State | Next decision |
|---|---|---|
| hybrid reply retrieval | `offline_only` | 2026-10-01 |
| generated-image identity policy | `suspended` | 2026-08-15 |
| quotation/image semantic veto | `active_shadow` | 2026-08-01 |
| original-editorial selector | `active_shadow` | 2026-08-01 |

No date is overdue as of 19 July 2026. A review defect was corrected: the digest now calculates and renders overdue lifecycle decisions rather than merely validating date syntax and silently omitting lateness. Invalid state names, malformed records and invalid dates still fail closed.

Lifecycle register SHA-256:

`a1cb7d6d77680786b4eda21ee01cb1dff6e59398b62164b0d65aa895cb3f1f40`

## Historical-context uncertainty

Wording-ineligible records remain in the research corpus. The regular-post gate applies only to unattended quotation selection and its derived runtime manifests. Historical-context formatting can still use such packets where the existing path permits them and renders explicit wording uncertainty, including `Verification: Exact wording not verified`. Focused regression coverage passes.

## Candidate manifests and hashes

Wording-filtered candidate after the ten corrections:

| Property | Value |
|---|---:|
| eligible quotes | 545 |
| wording-ineligible quotes | 65 |
| images | 91 |
| known pairs | 19,730 |
| allow | 19,615 |
| veto | 115 |
| quotes with a known allow | 544 |
| quotes with no known allow | 1 |
| excluded unknown pair records | 104 |
| AI calls | 0 |
| network calls | 0 |

Hashes:

| File | SHA-256 |
|---|---|
| `runtime_regular_post_eligibility_manifest.json` | `3ca20cc1802f42c59658ef2607b6acd4443b818b35f859402202ebc196f2a085` |
| `material_veto_v3_wording_verified_shadow_manifest.json` | `6f9f268ce4b917129e0689eab7a80d2e631e723153e82c114d75c7f9ae59a859` |
| `manifest_audit.json` | `814601f36012dcf7cbf3e1458018a9ae83b2507440e4aa825281f00abb4c2e56` |
| active 610-quote v3 manifest | `7016458cf516487f987188c73331b7d9af2f3e2518988c3b995785745cfd0170` |
| lifecycle register | `a1cb7d6d77680786b4eda21ee01cb1dff6e59398b62164b0d65aa895cb3f1f40` |

The wording-filtered files are isolated deployment candidates and were not installed.

## Commands and test results

Principal commands:

- direct Git diff, status, source and call-site searches;
- structured packet, contract and manifest parsing;
- `python3 quote_eligibility.py build --output semantic_alignment_research/quote_wording_eligibility_001`;
- two offline hybrid `evaluate` runs in copied `/tmp` directories;
- focused pytest groups;
- targeted `py_compile`;
- complete offline pytest suite;
- `git diff --check`;
- before/after `systemctl --user show` and process-tree checks.

Results:

| Validation | Result |
|---|---|
| consolidation review tests | 21 passed |
| hybrid/generated/veto/harness/digest focused group | 256 passed |
| reply/research/unit focused group | 606 passed |
| order-dependent reproduction | 19 passed |
| first complete suite | 1 test-fixture failure, 1,965 passed, 1 skipped |
| corrected complete suite | **1,966 passed, 1 skipped** in 774.64 seconds |
| targeted compilation | passed |
| `git diff --check` | passed |
| external AI/provider calls | 0 |
| simulator/benchmark network calls | 0 |

The first complete-suite failure was in the newly added cycle-transition test. An earlier offline backtest imports the bot with a temporary base directory; the test had not rebound its read-only corpus paths. The fixture was corrected and then passed both the direct order reproduction and the complete suite. No production code changed for that failure.

The expected skip and three dependency deprecation warnings are unchanged and unrelated.

## Modified files

Cumulative consolidation plus this independent review:

- `README.md`
- `docs/python_api.md`
- `mrsMThatcher.local.example.json`
- `mrsMThatcher2.py`
- `mrs_log_digest.py`
- `quote_eligibility.py`
- `quote_image_selection_harness.py`
- `semantic_alignment/hybrid_reply_retrieval.py`
- `semantic_alignment/quote_image_semantic_veto.py`
- `semantic_alignment_research/hybrid_reply_retrieval_001/OFFLINE_BENCHMARK.md`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/material_veto_v3_shadow_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/runtime_eligible_quote_manifest.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.json`
- `semantic_alignment_research/quote_attribution_cleanup_001/deployment_candidate/v3_shadow_manifest_audit.md`
- `semantic_alignment_research/quote_wording_eligibility_001/deployment_candidate/manifest_audit.json`
- `semantic_alignment_research/quote_wording_eligibility_001/deployment_candidate/manifest_audit.md`
- `semantic_alignment_research/quote_wording_eligibility_001/deployment_candidate/material_veto_v3_wording_verified_shadow_manifest.json`
- `semantic_alignment_research/quote_wording_eligibility_001/deployment_candidate/runtime_regular_post_eligibility_manifest.json`
- `shadow_feature_lifecycle.json`
- `shadow_functionality_consolidation_report.md`
- `shadow_functionality_consolidation_independent_review_report.md`
- `shadow_lifecycle.py`
- `tests/test_generated_identity_policy_production_scoring.py`
- `tests/test_generated_identity_policy_shadow_scoring.py`
- `tests/test_hybrid_reply_retrieval.py`
- `tests/test_quote_image_selection_harness.py`
- `tests/test_quote_research_corpus.py`
- `tests/test_reply_strategy.py`
- `tests/test_shadow_functionality_consolidation.py`
- `tests/test_unit_helpers.py`

The earlier `shadow_functionality_consolidation_report.md` records the pre-review 535 count and is retained as historical work product. This independent report supersedes its eligibility counts.

## Production-service proof

Before review validation:

- wrapper PID: `4025393`
- Python child PID: `4025394`
- start time: `Sun 2026-07-19 12:29:24 BST`
- restart count: `0`
- state: `active/running`

After all tests and report preparation:

- wrapper PID: `4025393`
- Python child PID: `4025394`
- start time: `Sun 2026-07-19 12:29:24 BST`
- restart count: `0`
- state: `active/running`

The service was not stopped, restarted, reloaded or signalled.

## Safety confirmation

There was no:

- X posting or test post;
- write to production posting state, receipts, ledgers, histories or analytics;
- production-service stop, restart, reload or signal;
- generated-image activation;
- semantic-veto enforcement;
- original-editorial activation or enforcement change;
- hybrid-retrieval activation;
- AI or provider call;
- commit, push or deployment.

## Remaining blocker

The filtered semantic-veto deployment candidate overstates incomplete coverage as a global no-safe-image conclusion for the Gorbachev quotation. Deploying it would make shadow reporting materially misleading even though enforcement is disabled. Correct candidate coverage, or explicitly represent the result as “no known allowed image among adjudicated pairs”, then regenerate and revalidate the candidate before deployment.

NOT READY FOR DEPLOYMENT
