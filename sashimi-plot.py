#!/usr/bin/env python

# Import modules
from argparse import ArgumentParser
#from subprocess import Popen, PIPE
import subprocess as sp
import sys, re
import operator


def define_options():
	# Argument parsing
	parser = ArgumentParser(description='Create sashimi plot for a given genomic region')
	parser.add_argument("-b", "--bam", type=str, 
		help="Individual bam file or file with a list of bam files and ids")
	parser.add_argument("-c", "--coordinates", type=str,
		help="Genomic region. Format: chr:start-end. Remember that bam coordinates are 0-based")
	parser.add_argument("-M", "--min_coverage", type=int, default=1, 
		help="Minimum number of reads supporting a junction to be drawn [default=1]")
	parser.add_argument("-g", "--gtf", 
		help="Gtf file with annotation (only exons is enough)")
	parser.add_argument("-s", "--strand", default="NONE", type=str, 
		help="Strand specificity: <NONE> <SENSE> <ANTISENSE> <MATE1_SENSE> <MATE2_SENSE> [default=%(default)s]")
	parser.add_argument("--shrink", action="store_true",  
		help="Shrink the junctions by a factor for nicer display [default=%(default)s]")
	parser.add_argument("-O", "--overlay", type=int, 
		help="Index of column with overlay levels (1-based)")
	parser.add_argument("-C", "--color-factor", type=int, dest="color_factor",
		help="Index of column with color levels (1-based)")
	parser.add_argument("--height", type=int, default=6,
		help="Height of the plot in inches [default=%(default)s]")
	parser.add_argument("--width", type=int, default=10,
		help="Width of the plot in inches [default=%(default)s]")
	parser.add_argument("--base-size", type=int, default=14, dest="base_size",
		help="Base character size of the plot in pch [default=%(default)s]")
#	parser.add_argument("-s", "--smooth", action="store_true", default=False, help="Smooth the signal histogram")
	return parser



def parse_coordinates(c):
	chr = c.split(":")[0]
	start, end = c.split(":")[1].split("-")
	# Convert to 0-based 
	start, end = int(start) - 1, int(end)
	return chr, start, end



def count_operator(CIGAR_op, CIGAR_len, pos, start, end, a, junctions, line):

	# Match
	if CIGAR_op == "M":
		for i in range(pos, pos + CIGAR_len):
			if i < start or i >= end:
				continue
			ind = i - start
			a[ind] += 1

	# Insertion or Soft-clip
	if CIGAR_op == "I" or CIGAR_op == "S":
		return pos

	# Deletion 
	if CIGAR_op == "D":
		pass

	# Junction
	if CIGAR_op == "N":
		don = pos
		acc = pos + CIGAR_len
		if don > start and acc < end:
			junctions[(don,acc)] = junctions.setdefault((don,acc), 0) + 1

	pos = pos + CIGAR_len

	return pos


def flip_read(s, samflag):
	if s == "NONE" or s == "SENSE":
		return 0
	if s == "ANTISENSE":
		return 1
	if s == "MATE1_SENSE":
		if int(samflag) & 64:
			return 0
		if int(samflag) & 128:
			return 1
	if s == "MATE2_SENSE":
		if int(samflag) & 64:
			return 1
		if int(samflag) & 128:
			return 0


def read_bam(f, c, s):

	chr, start, end = parse_coordinates(c)

	# Initialize coverage array and junction dict
	a = {"+" : [0] * (end - start)}
	junctions = {"+": {}}
	if s != "NONE":
		a["-"] = [0] * (end - start)
		junctions["-"] = {}

	p = sp.Popen("samtools view %s %s " %(f, c), shell=True, stdout=sp.PIPE)
	for line in p.communicate()[0].strip().split("\n"):

		line_sp = line.strip().split("\t")
		samflag, read_start, CIGAR = line_sp[1], int(line_sp[3]), line_sp[5]

		# Ignore reads with more exotic CIGAR operators
		if any(map(lambda x: x in CIGAR, ["H", "P", "X", "="])): 
			continue

		read_strand = ["+", "-"][flip_read(s, samflag) ^ bool(int(samflag) & 16)]
		if s == "NONE": read_strand = "+"

		CIGAR_lens = re.split("[MIDNS]", CIGAR)[:-1]
		CIGAR_ops = re.split("[0-9]+", CIGAR)[1:]

		pos = read_start

		for n, CIGAR_op in enumerate(CIGAR_ops):
			CIGAR_len = int(CIGAR_lens[n])
			pos = count_operator(CIGAR_op, CIGAR_len, pos, start, end, a[read_strand], junctions[read_strand], line=line)

	p.stdout.close()
	
	return a, junctions


def read_bam_input(f, overlay, color):
	if f.endswith(".bam"):
		bn = f.strip().split("/")[-1].strip(".bam")
		yield [(bn, f, None, None)]
	with open(f) as openf:
		for line in openf:
			line_sp = line.strip().split("\t")
			overlay_level = line_sp[overlay-1] if overlay else None
			color_level = line_sp[color-1] if color else None
			yield line_sp[0], line_sp[1], '"%s"' %(overlay_level), '"%s"' %(color_level)


def prepare_for_R(a, junctions, c, m):

	chr, start, end = parse_coordinates(args.coordinates)

	# Convert the array index to genomic coordinates
	x = list(i+start for i in range(len(a)))
	y = a

	# Arrays for R
	dons, accs, yd, ya, counts = [], [], [], [], []

	# Prepare arrays for junctions (which will be the arcs)
	for (don, acc), n in junctions.iteritems():

		# Do not add junctions with less than defined coverage
		if n < m:
			continue

		dons.append(don)
		accs.append(acc)
		counts.append(n)

		yd.append( a[ don - start -1 ])
		ya.append( a[ acc - start +1 ])

	return x, y, dons, accs, yd, ya, counts


def intersect_introns(data):
	data = sorted(data)
	it = iter(data)
	a, b = next(it)
	for c, d in it:
		if b > c:  # Use `if b > c` if you want (1,2), (2,3) not to be
			        # treated as intersection.
			b = min(b, d)
			a = max(a, c)
		else:
			yield a, b
			a, b = c, d
	yield a, b


def shrink_annotation(ann):
	return


def shrink_density(x, y, introns):
	new_x, new_y = [], []
	shift = 0
	start = 0
	# introns are already sorted by coordinates
	for a,b in introns:
		end = x.index(a)+1
		new_x += [int(i-shift) for i in x[start:end]]
		new_y += y[start:end]
		start = x.index(b)
		l = (b-a)
		shift += l-l**0.7
	new_x += [i-shift for i in x[start:]]
	new_y += y[start:]
	return new_x, new_y

def shrink_junctions(dons, accs, introns):
	new_dons, new_accs = [0]*len(dons), [0]*len(accs)
	shift_acc = 0 
	shift_don = 0
	s = set()
	junctions = zip(dons, accs)
	for a,b in introns:
		l = b - a
		shift_acc += l-int(l**0.7)
		for i, (don, acc) in enumerate(junctions):
			if a >= don and b <= acc:
				if (don,acc) not in s:
					new_dons[i] = don - shift_don
					new_accs[i] = acc - shift_acc
				else:
					new_accs[i] = acc - shift_acc
				s.add((don,acc))
		shift_don = shift_acc
	return new_dons, new_accs

def read_gtf(f, c):
	exons = {}
	transcripts = {}
	introns = {}
	chr, start, end = parse_coordinates(c)
	with open(f) as openf:
		for line in openf:
			if line.startswith("#"):
				continue
			el_chr, ann, el, el_start, el_end, score1, strand, score2, tags = line.strip().split("\t")
			if el_chr != chr:
				continue
			d = dict(kv.strip().split(" ") for kv in tags.strip(";").split("; "))
			transcript_id = d["transcript_id"]
			el_start, el_end = map(int, (el_start, el_end))
			strand = '"' + strand + '"'
			if el == "transcript":
				if (el_end > start and el_start < end):
					transcripts[transcript_id] = max(start, el_start), min(end, el_end)
				continue
			if el == "exon":
				if (start < el_start < end or start < el_end < end):
					exons.setdefault(transcript_id, []).append((max(el_start, start), min(end, el_end), strand))

	for tx, (tx_start,tx_end) in transcripts.iteritems():
		intron_start = tx_start
		for ex_start, ex_end, strand in sorted(exons[tx]):
			intron_end = ex_start
			if tx_start < ex_start:
				introns.setdefault(tx, []).append((intron_start, intron_end, strand))
			intron_start = ex_end
		if tx_end > ex_end:
			introns.setdefault(tx, []).append((intron_start, tx_end, strand))
					
	d = {'transcripts': transcripts, 'exons': exons, 'introns': introns}
	return d


def gtf_for_ggplot(annotation, c, arrow_bins):
	chr, start, end = parse_coordinates(c)
	arrow_space = (end - start)/arrow_bins
	s = """

	# data table with exons
	ann_list = list(
		'exons' = data.table(
			tx = rep(c(%(tx_exons)s), c(%(n_exons)s)), 
			start = c(%(exon_start)s),
			end = c(%(exon_end)s),
			strand = c(%(strand)s)
		),
		'introns' = data.table(
			tx = rep(c(%(tx_introns)s), c(%(n_introns)s)), 
			start = c(%(intron_start)s),
			end = c(%(intron_end)s),
			strand = c(%(strand)s)
		)
	)


	# Create data table for strand arrows
	txarrows = data.table()
	introns = ann_list[['introns']]
	# Add right-pointing arrows for plus strand
	if ("+" %%in%% introns$strand) {
		txarrows = rbind(
			txarrows,
			introns[strand=="+", list(
				seq(start+4,end,by=%(arrow_space)s)-1, 
				seq(start+4,end,by=%(arrow_space)s)
				), by=.(tx,start,end)
			]
		)
	}
	# Add left-pointing arrows for minus strand
	if ("-" %%in%% introns$strand) {
		txarrows = rbind(
			txarrows,
			introns[strand=="-", list(
				seq(start,max(start+1, end-4), by=%(arrow_space)s), 
				seq(start,max(start+1, end-4), by=%(arrow_space)s)-1
				), by=.(tx,start,end)
			]
		)
	}
	
	gtfp = ggplot()
	gtfp = gtfp + geom_segment(data=ann_list[['introns']], aes(x=start, xend=end, y=tx, yend=tx), size=0.3)
	gtfp = gtfp + geom_segment(data=txarrows, aes(x=V1,xend=V2,y=tx,yend=tx), arrow=arrow(length=unit(0.02,"npc")))
	gtfp = gtfp + geom_segment(data=ann_list[['exons']], aes(x=start, xend=end, y=tx, yend=tx), size=5, alpha=1)
	gtfp = gtfp + scale_y_discrete(expand=c(0,0.5))
	""" %({
		"tx_exons": ",".join(annotation["exons"].keys()),
		"n_exons": ",".join(map(str, map(len, annotation["exons"].itervalues()))),
		"exon_start" : ",".join(map(str, (v[0] for vs in annotation["exons"].itervalues() for v in vs))),
		"exon_end" : ",".join(map(str, (v[1] for vs in annotation["exons"].itervalues() for v in vs))),
		"strand" : ",".join(map(str, (v[2] for vs in annotation["exons"].itervalues() for v in vs))),

		"tx_introns": ",".join(annotation["introns"].keys()),
		"n_introns": ",".join(map(str, map(len, annotation["introns"].itervalues()))),
		"intron_start" : ",".join(map(str, (v[0] for vs in annotation["introns"].itervalues() for v in vs))),
		"intron_end" : ",".join(map(str, (v[1] for vs in annotation["introns"].itervalues() for v in vs))),
		"strand" : ",".join(map(str, (v[2] for vs in annotation["introns"].itervalues() for v in vs))),
		"arrow_space" : arrow_space,
	})
	return s


def setup_R_script(h, w, b):
	s = """
	library(ggplot2)
	library(grid)
	library(data.table)

	scale_lwd = function(r) {
		lmin = 0.1
		lmax = 4
		return( r*(lmax-lmin)+lmin )
	}

	height = %(h)s
	width = %(w)s
	base_size = %(b)s
	theme_set(theme_bw(base_size=base_size))
	theme_update(
		panel.grid = element_blank()
	)

	density_list = list()
	junction_list = list()
	""" %({
		'h': h,
		'w': w,
		'b': b,
	})
	return s

def density_overlay(d, R_list):
#	lapply(names(l), function(x) cbind(l[[`x`]], id=x))
#	setNames(lapply(levels(as.factor(names(v))), function(y) {rbindlist(lapply(v[which(names(v)==y)], function(x) d[[as.character(x)]]))}), levels(as.factor(names(v))))
	s = """
	f = data.frame(id=c(%(id)s), fac=rep(c(%(levels)s), c(%(length)s)))
	%(R_list)s = setNames(
		lapply(
			levels(f$fac), function(y) {
				rbindlist(lapply(subset(f, fac==y)$id, function(x) %(R_list)s[[as.character(x)]]))
			}
		), 
		levels(f$fac)
	)
	""" %({
		"levels": ",".join(d.keys()),
		"id": ",".join(map(str, ('"%s"' %(v) for vs in d.itervalues() for v in vs))),
		"length": ",".join(map(str, map(len, d.values()))),
		"R_list": R_list,
	})
	return s


def plot(R_script):
	p = sp.Popen("R --vanilla --slave", shell=True, stdin=sp.PIPE)
	p.communicate(input=R_script)
	p.stdin.close()
	p.wait()
	return


def colorize(d, p, color_factor):
	levels = sorted(set(d.itervalues()))
	n = len(levels)
	if n > len(p):
		p = (p*n)[:n]
	if color_factor:
		s = "color_list = list(%s)\n" %( ",".join('%s="%s"' %(k, p[levels.index(v)]) for k,v in d.iteritems()) )
	else:
		s = "color_list = list(%s)\n" %( ",".join('%s="%s"' %(k, "grey") for k,v in d.iteritems()) )
	return s



if __name__ == "__main__":

	parser = define_options()
	args = parser.parse_args()
	
#	args.coordinates = "chrX:9609491-9612406"
#	args.coordinates = "chrX:9609491-9610000"
#	args.bam = "/nfs/no_backup/rg/epalumbo/projects/tg/work/8b/8b0ac8705f37fd772a06ab7db89f6b/2A_m4_n10_toGenome.bam"

	if args.gtf:
		annotation = read_gtf(args.gtf, args.coordinates)


	bam_dict, overlay_dict, color_dict = {"+":{}}, {}, {}
	if args.strand != "NONE": bam_dict["-"] = {}
	for id, bam, overlay_level, color_level in read_bam_input(args.bam, args.overlay, args.color_factor):
		a, junctions = read_bam(bam, args.coordinates, args.strand)
		for strand in a:
		 	bam_dict[strand][id] = prepare_for_R(a[strand], junctions[strand], args.coordinates, args.min_coverage)
		if overlay_level != '"None"':
			overlay_dict.setdefault(overlay_level, []).append(id)
			if color_level:
				color_dict.setdefault(overlay_level, overlay_level)
		if overlay_level == '"None"':
			color_dict.setdefault(id, color_level)

	

	for strand in bam_dict:	
		if args.shrink:
			introns = (v for vs in bam_dict[strand].values() for v in zip(vs[2], vs[3]))
			intersected_introns = list(intersect_introns(introns))

		R_script = setup_R_script(args.height, args.width, args.base_size)
		palette = "#ff0000", "#000000", "#00ff00"

		R_script += colorize(color_dict, palette, args.color_factor)
	
		arrow_bins = 50
		if args.gtf:
#			if args.shrink:
#				annotation = shrink_annotation(annotation)
			R_script += gtf_for_ggplot(annotation, args.coordinates, arrow_bins)
			
			
		for k, v in bam_dict[strand].iteritems():
			x, y, dons, accs, yd, ya, counts = v
			if args.shrink:
				x, y = shrink_density(x, y, intersected_introns)
				dons, accs = shrink_junctions(dons, accs, intersected_introns)
#				dons, accs, yd, ya, counts = [], [], [], [], []			

			R_script += """
			density_list$%(id)s = data.frame(x=c(%(x)s), y=c(%(y)s))
			junction_list$%(id)s = data.frame(x=c(%(dons)s), xend=c(%(accs)s), y=c(%(yd)s), yend=c(%(ya)s), count=c(%(counts)s))
			""" %({
				"id": k,
				'x' : ",".join(map(str, x)),
				'y' : ",".join(map(str, y)),
				'dons' : ",".join(map(str, dons)),
				'accs' : ",".join(map(str, accs)),
				'yd' : ",".join(map(str, yd)),
				'ya' : ",".join(map(str, ya)),
				'counts' : ",".join(map(str, counts)),
			})

		if args.overlay:
			R_script += density_overlay(overlay_dict, "density_list")
			R_script += density_overlay(overlay_dict, "junction_list")
	
		R_script += """
	
		pdf("%(out)s", h=height, w=10)
		grid.newpage()
		pushViewport(viewport(layout = grid.layout(length(density_list)+%(args.gtf)s, 1)))
	
		for (bam_index in 1:length(density_list)) {
		
			id = names(density_list)[bam_index]
			d = density_list[[id]]
			junctions = junction_list[[id]]
		
			maxheight = max(d[['y']])
		
			# Density plot
			gp = ggplot(d) + geom_bar(aes(x, y), position='identity', stat='identity', fill=color_list[[id]], alpha=1/2)
			gp = gp + labs(title=id)
	

			if (nrow(junctions)>0) {row_i = 1:nrow(junctions)} else {row_i = c()}


			for (i in row_i) {

				j_tot_counts = sum(junctions[['count']])
		
				j = as.numeric(junctions[i,])
		
				# Find intron midpoint 
				xmid = round(mean(j[1:2]), 1)
				ymid = max(j[3:4]) * 1.1
		
				# Thickness of the arch
				lwd = scale_lwd(j[5]/j_tot_counts)
		
				curve_par = gpar(lwd=lwd, col=color_list[[id]])
		
				# Choose position of the arch (top or bottom)
#				nss = sum(junctions[,1] %%in%% j[1])
#				nss = i
				nss = 1
				if (nss%%%%2 == 0) {  #bottom
					ymid = -0.4 * maxheight
					# Draw the arcs
					# Left
					curve = xsplineGrob(x=c(0, 0, 1, 1), y=c(1, 0, 0, 0), shape=1, gp=curve_par)
					gp = gp + annotation_custom(grob = curve, j[1], xmid, 0, ymid)
					# Right
					curve = xsplineGrob(x=c(1, 1, 0, 0), y=c(1, 0, 0, 0), shape=1, gp=curve_par)
					gp = gp + annotation_custom(grob = curve, xmid, j[2], 0, ymid)
				} 
		
				if (nss%%%%2 != 0) {  #top
					# Draw the arcs
					# Left
					curve = xsplineGrob(x=c(0, 0, 1, 1), y=c(0, 1, 1, 1), shape=1, gp=curve_par)
					gp = gp + annotation_custom(grob = curve, j[1], xmid, j[3], ymid)
					# Right
					curve = xsplineGrob(x=c(1, 1, 0, 0), y=c(0, 1, 1, 1), shape=1, gp=curve_par)
					gp = gp + annotation_custom(grob = curve, xmid, j[2], j[4], ymid)
			
					gp = gp + annotate("label", x = xmid, y = ymid, label = j[5], 
						vjust=0.5, hjust=0.5, label.padding=unit(0.01, "lines"), 
						label.size=NA, size=(base_size*0.352777778)*0.6
					)
				}
		
	
		#		gp = gp + annotation_custom(grob = rectGrob(x=0, y=0, gp=gpar(col="red"), just=c("left","bottom")), xmid, j[2], j[4], ymid)
		#		gp = gp + annotation_custom(grob = rectGrob(x=0, y=0, gp=gpar(col="green"), just=c("left","bottom")), j[1], xmid, j[3], ymid)
		
		
			}
			print(gp, vp=viewport(layout.pos.row = bam_index, layout.pos.col = 1))
		}
	
		if (%(args.gtf)s == 1) {
			print(gtfp, vp=viewport(layout.pos.row = bam_index+1, layout.pos.col = 1))
		}
		
	
		dev.off()
	
		""" %({
			"out": "tmp_%s.pdf" %strand, 
			"args.gtf": int(bool(args.gtf)),
			"height": 6,
			})
	
	
		plot(R_script)
	exit()






