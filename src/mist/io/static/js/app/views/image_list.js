define('app/views/image_list', [
    'app/views/mistscreen',
    'text!app/templates/image_list.html','ember'],
    /**
     *
     * Images page
     *
     * @returns Class
     */
    function(MistScreen, image_list_html) {
        return MistScreen.extend({
	        template: Ember.Handlebars.compile(image_list_html),
	        init: function(){
	        	this._super();
	        	
	            warn('init images');
	            $(window).on('scroll', this.didScroll);
	        },
	       
            willDestroyElement: function(){
            	$(window).off('scroll');
   			},
   			
						
			// this is called every time we scroll
			didScroll: function(){
				warn('scroscropllll');

			    if (Mist.isScrolledToBottom()) {
			  	    warn('get more more');
			  	    var items = Mist.backendsController.content;
			  	   
			  	   //We loop through the images of every backend and append 20 at a time 
			  	   var counter = 0;
			  	    for (var i = 0; i < items.length; i++) {
			  	    	for (var j = 0; j < items[i].images.content.length; j ++) {
			  	    		if(!(items[i].images.content[j].star) &&  Mist.renderedImages.content.indexOf(items[i].images.content[j]) == -1 && counter < 20) {
			  	    			Mist.renderedImages.pushObject(items[i].images.content[j]);
			  	    			counter++;

			  	    		}
			  	    	}
			  	    }
			  	    //Mist.renderedImages.content.pushObject(Mist.backendsController.content[0].images.content[300]);
			  	    //Ember.set('Mist.renderedImages', 0);

			  	    //this.get('controller').send('getMore');
			    }
			},
			getMore: function() {
			    //don't load new data if we already are
			    if(this.get('loadingMore')) return;
			    
			    this.set('loadingMore', true);
			    
			    this.get('target').set('getMore');    
			},
			
			isScrolledToBottom: function() {
    			var distanceToTop = $(document).height() - $(window).height(),
     		    top = $(document).scrollTop();

    			return top === distanceToTop;
  			},
  			
  			loadingMore: function(){
  				
  			},
			
			gotMore: function() {
			    this.set('loadingMore', false);
			    
			    
			}
           
        });
    }
);
